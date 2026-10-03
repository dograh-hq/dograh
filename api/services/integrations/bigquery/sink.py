"""BigQuery adapter for the call event contract."""

import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.services.observability.call_events.base import ExportResult
from api.services.observability.call_events.events import CallEvent

SCOPES = ["https://www.googleapis.com/auth/bigquery"]
RETRYABLE_REASONS = {
    "backendError",
    "internalError",
    "rateLimitExceeded",
    "quotaExceeded",
    "timeout",
    "stopped",
}
REQUIRED_COLUMNS = {
    "ts": {"TIMESTAMP"},
    "event": {"STRING"},
    "run_id": {"INTEGER", "INT64"},
    "org_id": {"INTEGER", "INT64"},
    "workflow_id": {"INTEGER", "INT64"},
    "turn": {"INTEGER", "INT64"},
    "severity": {"STRING"},
    "value_ms": {"FLOAT", "FLOAT64"},
    "node_id": {"STRING"},
    "node_name": {"STRING"},
    "detail": {"JSON", "STRING"},
}


class BigQueryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    table: str = Field(
        pattern=r"^[a-z][a-z0-9-]{4,61}[a-z0-9]\.[A-Za-z0-9_]{1,1024}\.[A-Za-z0-9_]{1,1024}$"
    )
    auth_mode: Literal["service_account", "application_default"] = "service_account"
    client_email: str = Field(default="", max_length=320)
    private_key: str = Field(default="", repr=False, max_length=16384)

    @model_validator(mode="after")
    def validate_auth(self):
        from api.constants import AUTH_PROVIDER

        if self.auth_mode == "application_default":
            if AUTH_PROVIDER != "local":
                raise ValueError(
                    "Deployment identity is available only on self-hosted installations"
                )
            self.client_email = ""
            self.private_key = ""
        else:
            if (
                not self.client_email.endswith(".iam.gserviceaccount.com")
                or "@" not in self.client_email
            ):
                raise ValueError("Enter a Google service account email")
            if not self.private_key:
                raise ValueError("Enter the service account private key")
            try:
                self.credentials()
            except Exception:
                raise ValueError("Invalid service account private key") from None
        return self

    def credentials(self):
        import google.auth
        from google.oauth2 import service_account

        if self.auth_mode == "application_default":
            return google.auth.default(scopes=SCOPES)[0]
        return service_account.Credentials.from_service_account_info(
            {
                "type": "service_account",
                "client_email": self.client_email,
                "private_key": self.private_key,
                "token_uri": "https://oauth2.googleapis.com/token",
            },
            scopes=SCOPES,
        )


def to_bigquery_row(event: CallEvent) -> dict:
    row = asdict(event)
    event_id = row.pop("event_id")
    row["ts"] = (
        datetime.fromtimestamp(event.ts, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    try:
        detail = json.dumps(event.detail, default=str)
    except (TypeError, ValueError):
        detail = json.dumps({"unserialisable": True})
    if len(detail) > 1024:
        detail = json.dumps({"truncated": True, "chars": len(detail)})
    row["detail"] = detail
    return {"insertId": event_id, "json": row}


class BigQuerySink:
    def __init__(self, config: BigQueryConfig):
        self.config = config
        project, dataset, table = config.table.split(".")
        self.url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/datasets/{dataset}/tables/{table}"
        self._client = httpx.AsyncClient(timeout=10, follow_redirects=False)
        self._credentials = None

    async def _headers(self) -> dict[str, str]:
        def authorize():
            from google.auth.transport.requests import Request

            if self._credentials is None:
                self._credentials = self.config.credentials()
            if not self._credentials.valid:
                # Google auth is synchronous; keep refresh off the audio loop.
                request = Request()

                def bounded_request(*args, **kwargs):
                    kwargs["timeout"] = 10
                    return request(*args, **kwargs)

                self._credentials.refresh(bounded_request)
            return {"Authorization": f"Bearer {self._credentials.token}"}

        return await asyncio.to_thread(authorize)

    async def validate_connection(self) -> None:
        response = await self._client.get(self.url, headers=await self._headers())
        if response.status_code != 200:
            raise ValueError(
                "Cannot read the BigQuery table. Check credentials, table name and permissions."
            )
        fields = {
            f["name"]: f for f in response.json().get("schema", {}).get("fields", [])
        }
        schema_error = "The table schema does not match the call events schema: "
        for name, types in REQUIRED_COLUMNS.items():
            if name not in fields:
                raise ValueError(f"{schema_error}Missing column '{name}'")
            field = fields[name]
            if field.get("type") not in types:
                expected = " or ".join(sorted(types))
                raise ValueError(
                    f"{schema_error}Column '{name}' has type {field.get('type')}; "
                    f"expected {expected}"
                )
            if field.get("mode") == "REPEATED":
                raise ValueError(f"{schema_error}Column '{name}' must not be REPEATED")
        if any(
            f.get("mode") == "REQUIRED" and name not in REQUIRED_COLUMNS
            for name, f in fields.items()
        ):
            raise ValueError(
                "The table has additional required columns that call events cannot populate"
            )

    async def export(self, events: tuple[CallEvent, ...]) -> ExportResult:
        accepted, retryable, rejected = set(), set(), set()
        headers = await self._headers()
        for offset in range(0, len(events), 500):
            batch = events[offset : offset + 500]
            ids = {e.event_id for e in batch}
            try:
                response = await self._client.post(
                    self.url + "/insertAll",
                    headers=headers,
                    json={
                        "skipInvalidRows": True,
                        "ignoreUnknownValues": False,
                        "rows": [to_bigquery_row(e) for e in batch],
                    },
                )
            except httpx.TransportError:
                # A timeout has an unknown outcome. Stable IDs make retries
                # best-effort deduplicated, never exactly-once.
                retryable.update(ids)
                continue
            if response.status_code in {408, 429} or response.status_code >= 500:
                retryable.update(ids)
                continue
            if response.status_code != 200:
                rejected.update(ids)
                continue
            try:
                errors = response.json().get("insertErrors", [])
                failed = set()
                for error in errors:
                    index = error["index"]
                    if not isinstance(index, int) or not 0 <= index < len(batch):
                        raise ValueError("Invalid row error index")
                    event_id = batch[index].event_id
                    failed.add(event_id)
                    reasons = {e.get("reason") for e in error["errors"]}
                    (
                        retryable
                        if reasons and reasons <= RETRYABLE_REASONS
                        else rejected
                    ).add(event_id)
                accepted.update(ids - failed)
            except (ValueError, KeyError, TypeError, AttributeError):
                retryable.update(ids - accepted - rejected)
        return ExportResult(
            frozenset(accepted), frozenset(retryable - rejected), frozenset(rejected)
        )

    async def close(self) -> None:
        await self._client.aclose()
