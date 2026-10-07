from __future__ import annotations

from typing import Any

import httpx
from loguru import logger
from pydantic import BaseModel, field_validator

# Roark reads the recording itself, and the read can be queued behind its own
# work, so the POST only has to be accepted here. Ten seconds matches the other
# post-call exports in this package.
_REQUEST_TIMEOUT_SECONDS = 10

# Roark reports a refusal as `{"code": ..., "message": ...}`, and that message is
# the only part of a response body this integration repeats. An arbitrary body is
# never echoed: the request carries a transcript and a caller's number, so a
# gateway that reflects it back would otherwise put both in the logs and in the
# run's annotations.


class RoarkDeliveryError(Exception):
    """Roark refused the call. Carries the status so the caller can report it."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"Roark returned {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class RoarkDeliveryConfig(BaseModel):
    base_url: str
    api_key: str

    @field_validator("api_key")
    @classmethod
    def _must_not_be_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be empty")
        return value


async def create_call(
    config: RoarkDeliveryConfig,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """POST one finished call to Roark.

    Returns the created call's id and project id on success, or a `duplicate`
    status when Roark already holds this run. A refusal raises
    `RoarkDeliveryError` with Roark's own message, because the useful ones are
    actionable by the person who configured the node: an expired key, a
    recording Roark could not fetch, or an organization out of credit.
    """
    url = f"{config.base_url.rstrip('/')}/v1/call"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    external_id = payload.get("externalId")
    logger.info("[roark] posting completed call {} to Roark", external_id)

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.post(url, json=payload, headers=headers)

    # `externalId` is unique per Roark project, so a redelivered run is answered
    # with a 409. That is the export already having happened, not a failure, and
    # reporting it as an error would make a retried post-call job look broken.
    if response.status_code == 409:
        logger.info("[roark] call {} already exists in Roark", external_id)
        return {"status": "duplicate", "status_code": response.status_code}

    if response.status_code >= 400:
        detail = _error_detail(response)
        logger.error(
            "[roark] POST failed for call {} with status {}: {}",
            external_id,
            response.status_code,
            detail,
        )
        raise RoarkDeliveryError(response.status_code, detail)

    body = _json_body(response)
    data = body.get("data") if isinstance(body, dict) else None
    call_id = data.get("id") if isinstance(data, dict) else None

    logger.info(
        "[roark] POST succeeded for call {} with status {} (roark call {})",
        external_id,
        response.status_code,
        call_id,
    )
    return {
        "status": "delivered",
        "status_code": response.status_code,
        "roark_call_id": call_id,
        "roark_project_id": data.get("projectId") if isinstance(data, dict) else None,
    }


def _json_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _error_detail(response: httpx.Response) -> str:
    body = _json_body(response)
    if isinstance(body, dict):
        message = body.get("message") or body.get("detail")
        if isinstance(message, str) and message:
            return message
    # Not something Roark produced: a proxy or gateway answered instead.
    return f"Roark returned an unreadable {response.status_code} response"
