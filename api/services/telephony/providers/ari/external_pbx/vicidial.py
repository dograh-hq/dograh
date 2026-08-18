"""VICIdial implementation of external-PBX call and lead operations."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Mapping, Sequence

import aiohttp
from loguru import logger

from .base import ExternalPBXAdapter, ExternalPBXResult, HeaderReader

_LEAD_FIELD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
# Every control parameter update_lead sends. A mapped field sharing one of
# these names would override it, since mapped fields are appended last.
_RESERVED_LEAD_FIELDS = frozenset(
    {"source", "user", "pass", "function", "lead_id", "format", "custom_fields"}
)

_HEADER_PREFIX = "X-VICIDIAL-"
_IDENTITY_HEADER_FIELDS = ("callerid", "user", "lead_id", "campaign_id", "ingroup_id")
_CUSTOM_FIELDS_UPDATED = "CUSTOM FIELDS VALUES UPDATED"


def _response_code(response_text: str) -> str:
    """Return a diagnostic VICIdial status without retaining response PII."""
    if not response_text:
        return "empty"
    prefix = response_text.partition(":")[0].strip().casefold()
    if prefix in {"success", "error", "notice", "warning"}:
        return prefix
    return "unexpected"


def _update_applied(response_text: str) -> bool:
    """Whether VICIdial reports that ``update_lead`` changed anything.

    Standard columns and custom list fields are written by separate code paths
    in non_agent_api.php, and each announces itself on its own line:

        SUCCESS: update_lead LEAD HAS BEEN UPDATED - ...          (standard)
        NOTICE: update_lead CUSTOM FIELDS VALUES UPDATED - ...    (custom)

    A write touching only custom fields therefore never emits a SUCCESS line, so
    matching on that alone reports a working sync as a rejection.
    """
    return any(
        line.startswith("SUCCESS") or _CUSTOM_FIELDS_UPDATED in line
        for line in (raw.strip() for raw in response_text.splitlines())
    )


def _response_summary(response_text: str) -> str:
    """Return VICIdial's failure reason without the parameters it echoes back.

    ``response_code`` alone cannot separate a rejected field name from a missing
    lead or a revoked permission, so the reason itself is worth logging. What is
    not worth logging is the block VICIdial appends after it, which repeats the
    API user and the search values it was given:

        ERROR: update_lead NO MATCHES FOUND IN THE SYSTEM: |DOGRAHAPI|1||
        ERROR: update_lead NO VALID SEARCH METHOD - DOGRAHAPI|LEAD_ID|||

    Either ``|`` or `` - `` can introduce that block, so cut at whichever comes
    first and keep only the reason ahead of it.
    """
    if not response_text:
        return "<empty body>"
    reason = response_text
    for delimiter in ("|", " - "):
        reason = reason.partition(delimiter)[0]
    return reason.strip().rstrip(":-").strip()[:200]


class VicidialAdapter(ExternalPBXAdapter):
    type = "vicidial"
    header_prefix = _HEADER_PREFIX

    def __init__(self, config: dict[str, Any]):
        agent_api = config.get("agent_api") or {}
        non_agent_api = config.get("non_agent_api") or {}
        self._agent_url = str(agent_api.get("url", "")).strip()
        self._agent_user = str(agent_api.get("username", "")).strip()
        self._agent_password = str(agent_api.get("password", ""))
        self._agent_source = str(agent_api.get("source", "dograh")).strip()
        self._non_agent_url = str(non_agent_api.get("url", "")).strip()
        self._non_agent_user = str(non_agent_api.get("username", "")).strip()
        self._non_agent_password = str(non_agent_api.get("password", ""))
        self._non_agent_source = str(non_agent_api.get("source", "dograh")).strip()
        self._timeout = aiohttp.ClientTimeout(
            total=min(max(int(config.get("timeout_seconds", 8)), 1), 30)
        )

    async def capture_call_identity(
        self, read_header: HeaderReader, lead_fields: Sequence[str] = ()
    ) -> dict[str, Any] | None:
        # Each header is its own ARI request, so read a fixed set: the identity
        # fields the transfer/hangup paths need, plus whatever lead fields the
        # workflow configured. Enumerating X-VICIDIAL-* instead would cost an
        # extra request up front and one more per header VICIdial happens to
        # attach — all on the latency-sensitive inbound setup path.
        configured = [
            field
            for field in dict.fromkeys(
                value.strip() for value in lead_fields if isinstance(value, str)
            )
            if field
        ]
        requested = [
            field
            for field in configured
            if field not in _IDENTITY_HEADER_FIELDS and _LEAD_FIELD_RE.match(field)
        ]
        names = list(_IDENTITY_HEADER_FIELDS) + requested
        values = await asyncio.gather(
            *(read_header(_HEADER_PREFIX + field) for field in names)
        )
        headers = {field: value.strip() for field, value in zip(names, values)}

        # A configured field the PBX sent empty and one that was never asked for
        # both just go missing from `lead`, and those need opposite fixes: the
        # first is a PBX/lead-data problem, the second a workflow setting one.
        # Header values are customer PII, so report names and emptiness only.
        if configured:
            already_captured = [
                field for field in configured if field in _IDENTITY_HEADER_FIELDS
            ]
            logger.info(
                "[VICIdial] Lead header read: "
                f"requested={requested} "
                f"populated={[field for field in requested if headers.get(field)]} "
                f"empty={[field for field in requested if not headers.get(field)]}"
                + (f" already_captured={already_captured}" if already_captured else "")
            )
            invalid = [
                field
                for field in configured
                if field not in _IDENTITY_HEADER_FIELDS
                and not _LEAD_FIELD_RE.match(field)
            ]
            if invalid:
                logger.warning(
                    "[VICIdial] Ignoring invalid names in the workflow's Lead "
                    f"Fields To Capture setting: {invalid}"
                )
        callerid = headers.get("callerid", "")
        if not callerid:
            return None
        return {
            "type": self.type,
            "callerid": callerid,
            "agent_user": headers.get("user", ""),
            "lead_id": headers.get("lead_id", ""),
            "campaign_id": headers.get("campaign_id", ""),
            "ingroup_id": headers.get("ingroup_id", ""),
            "lead": {field: value for field, value in headers.items() if value},
        }

    async def _agent_call_control(
        self, identity: Mapping[str, Any], stage: str, **extra: str
    ) -> ExternalPBXResult:
        if not all([self._agent_url, self._agent_user, self._agent_password]):
            return ExternalPBXResult(
                False, stage.lower(), "Agent API is not configured"
            )
        if not identity.get("callerid") or not identity.get("agent_user"):
            return ExternalPBXResult(
                False, stage.lower(), "VICIdial call identity is incomplete"
            )
        params = {
            "source": self._agent_source,
            "user": self._agent_user,
            "pass": self._agent_password,
            "agent_user": identity["agent_user"],
            "function": "ra_call_control",
            "stage": stage,
            "value": identity["callerid"],
            **extra,
        }
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(self._agent_url, params=params) as response:
                    response_text = (await response.text()).strip()
                    ok = response.status == 200 and response_text.startswith("SUCCESS")
            logger.info(
                "[VICIdial] ra_call_control completed "
                f"stage={stage} status={response.status} ok={ok}"
            )
            return ExternalPBXResult(
                ok,
                stage.lower(),
                "VICIdial accepted the operation"
                if ok
                else "VICIdial rejected the operation",
            )
        except Exception as exc:
            logger.error(
                "[VICIdial] ra_call_control failed "
                f"stage={stage} error_type={type(exc).__name__}"
            )
            return ExternalPBXResult(
                False, stage.lower(), "VICIdial API request failed"
            )

    async def hangup(self, identity: Mapping[str, Any]) -> ExternalPBXResult:
        return await self._agent_call_control(identity, "HANGUP")

    async def transfer(
        self, identity: Mapping[str, Any], destination: str
    ) -> ExternalPBXResult:
        choice = destination.strip()
        if choice.lower() == "source":
            choice = str(identity.get("ingroup_id", "")).strip()
        if not choice:
            return ExternalPBXResult(
                False, "ingrouptransfer", "No VICIdial in-group was resolved"
            )
        return await self._agent_call_control(
            identity, "INGROUPTRANSFER", ingroup_choices=choice
        )

    async def update_fields(
        self, identity: Mapping[str, Any], fields: Mapping[str, str]
    ) -> ExternalPBXResult:
        # Each early return below ends the write silently otherwise, which reads
        # in the log exactly like no transfer having happened at all.
        if not fields:
            logger.info(
                "[VICIdial] update_lead skipped: no field mappings resolved to a "
                "non-empty value"
            )
            return ExternalPBXResult(True, "update_lead", "No lead fields configured")
        if not all(
            [self._non_agent_url, self._non_agent_user, self._non_agent_password]
        ):
            logger.warning(
                "[VICIdial] update_lead skipped: non-agent API is not configured "
                f"fields={sorted(fields)}"
            )
            return ExternalPBXResult(
                False, "update_lead", "Non-agent API is not configured"
            )
        lead_id = str(identity.get("lead_id", "")).strip()
        if not lead_id:
            logger.warning(
                "[VICIdial] update_lead skipped: no lead_id was captured from the "
                f"INVITE fields={sorted(fields)}"
            )
            return ExternalPBXResult(
                False, "update_lead", "No VICIdial lead ID captured"
            )

        safe_fields: dict[str, str] = {}
        for key, value in fields.items():
            normalized = str(key).strip()
            if (
                not _LEAD_FIELD_RE.fullmatch(normalized)
                or normalized.lower() in _RESERVED_LEAD_FIELDS
            ):
                logger.warning(
                    f"[VICIdial] Ignoring invalid lead field name: {normalized!r}"
                )
                continue
            safe_fields[normalized] = str(value)
        if not safe_fields:
            logger.warning(
                "[VICIdial] update_lead skipped: no configured destination field "
                f"survived validation fields={sorted(fields)}"
            )
            return ExternalPBXResult(
                False, "update_lead", "No valid lead fields resolved"
            )

        # Field order is load-bearing: VICIdial's custom-field parser ignores
        # whatever occupies the first query-string slot, so a mapped field
        # placed there is dropped without any error. Control parameters lead
        # and the mapped fields follow; _RESERVED_LEAD_FIELDS is what stops a
        # mapped field from overriding a control parameter from that position.
        params = {
            "source": self._non_agent_source,
            "user": self._non_agent_user,
            "pass": self._non_agent_password,
            "function": "update_lead",
            "lead_id": lead_id,
            # non_agent_api.php gates its result line on this parameter: without
            # it the update still runs but the response body is empty, which
            # reads here as a rejection. The agent API echoes unconditionally,
            # which is why only this call needs it.
            "format": "text",
            # Without this, VICIdial silently ignores every parameter that names
            # a custom list field — no update, no error, just an empty body. We
            # cannot know which destination fields a deployment defined as
            # custom, so it is always sent; a request carrying only standard
            # columns is unaffected by it.
            "custom_fields": "Y",
            **safe_fields,
        }
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(self._non_agent_url, params=params) as response:
                    response_text = (await response.text()).strip()
                    ok = response.status == 200 and _update_applied(response_text)
            logger.info(
                "[VICIdial] update_lead completed "
                f"status={response.status} ok={ok} "
                f"fields={sorted(safe_fields)} "
                f"response_code={_response_code(response_text)}"
            )
            if not ok:
                # The field names are workflow configuration, not lead data, so
                # naming them here is what separates "VICIdial does not know this
                # field" from a permission or lead-lookup failure.
                logger.warning(
                    "[VICIdial] update_lead rejected "
                    f"fields={sorted(safe_fields)} "
                    f"response={_response_summary(response_text)!r}"
                )
            return ExternalPBXResult(
                ok,
                "update_lead",
                "VICIdial lead updated" if ok else "VICIdial rejected the lead update",
            )
        except Exception as exc:
            logger.error(
                "[VICIdial] update_lead failed "
                f"fields={sorted(safe_fields)} error_type={type(exc).__name__}"
            )
            return ExternalPBXResult(
                False, "update_lead", "VICIdial API request failed"
            )
