from __future__ import annotations

import json
from typing import Any

import httpx
from loguru import logger
from pydantic import BaseModel, field_validator

# Roark reads the recording bytes asynchronously, but it does check the URL
# while the POST is open: a HEAD and then a ranged GET, back to this Dograh
# deployment, possibly out through a cloudflared tunnel. So the POST is not
# bounded by Roark's own work alone, and ten seconds (what the other post-call
# exports in this package use, none of which are called back) left no room for
# that round trip.
_REQUEST_TIMEOUT_SECONDS = 30

# Roark reports a refusal as `{"code": ..., "message": ...}`, and that pair is the
# only part of a response body this integration repeats. An arbitrary body is
# never echoed: the request carries a transcript and a caller's number, so a
# gateway that reflects it back would otherwise put both in the logs and in the
# run's annotations. `code` is what distinguishes Roark's own refusal from such a
# reflection, since every error Roark returns carries one.
_MAX_DETAIL_CHARS = 300

# How many rejected fields to name. A payload rejected for one reason usually
# repeats it per turn, so the first few are the whole story.
_MAX_REPORTED_ISSUES = 3


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

    # Only a 2xx is a delivery. A 3xx would otherwise fall through as one: this
    # client does not follow redirects, so a gateway answering 307 means the
    # call never reached Roark, and reporting `delivered` would hide that.
    if not 200 <= response.status_code < 300:
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
    """What to report about a refusal, with nothing reflected back into it.

    Roark's refusals are hand-written or derived from its own request schema
    (`transcript.0.content: Required`), never built from the value a caller
    sent, so repeating `code` and `message` is what makes a misconfigured node
    diagnosable. A body carrying no `code` did not come from Roark, and is
    reported by status alone rather than quoted: that is the shape a reflecting
    proxy produces, and the request it would be reflecting holds the transcript
    and the caller's number.

    The whole detail is bounded, not just the message: both halves come from
    the response body, so a long `code` would carry as much of one as a long
    message.
    """
    body = _json_body(response)
    if isinstance(body, dict):
        code = body.get("code")
        if isinstance(code, str) and code:
            message = body.get("message")
            if isinstance(message, str) and message:
                return f"{code}: {message}"[:_MAX_DETAIL_CHARS]
            return code[:_MAX_DETAIL_CHARS]
        rejected = _rejected_fields(body)
        if rejected:
            return f"validation: {rejected}"[:_MAX_DETAIL_CHARS]
    # Not from Roark: an intermediary answered, or Roark changed its error
    # shape. Either way there is nothing here safe to quote.
    return f"Roark returned an unreadable {response.status_code} response"


def _rejected_fields(body: dict[str, Any]) -> str | None:
    """The fields Roark's request schema rejected, as `path: message` pairs.

    A schema rejection does not go through Roark's own error handler: the
    request validator answers `{"success": false, "error": <the schema error>}`
    directly, with no `code` of its own. That is the most common 400 a
    misconfigured or out-of-date export gets, and reporting it as an unreadable
    response threw away the only thing that said what to change.

    An `error` with no readable issue list yields None rather than its
    `message`: nothing has identified the body as Roark's at that point, and
    `_error_detail` reports an unidentified body by status alone.

    Only `path` and `message` are read. A path is field names and array
    indices, never content, and these messages are the schema's own. The one
    that can name a value is an enum rejection, and every enum in this payload
    (`endedStatus`, `callDirection`, `interfaceType`, a participant role) is a
    value this package produced, not anything the call carried.
    """
    error = body.get("error")
    if not isinstance(error, dict):
        return None

    issues = error.get("issues")
    if not isinstance(issues, list):
        # Newer zod serialises the issue list as JSON inside `message` instead
        # of alongside it, so the same information arrives one level in.
        nested = _loads(error.get("message"))
        issues = nested if isinstance(nested, list) else None
    if not isinstance(issues, list):
        # A bare `error.message` that is not a serialised issue list is not a
        # shape Roark produces: its own refusals carry a `code`, and its schema
        # rejections carry `issues`. So this is an intermediary's prose, of
        # unknown provenance, about a request holding the transcript and the
        # caller's number. Reported by status alone, like any other body with
        # no `code`.
        return None

    reported = []
    for issue in issues[:_MAX_REPORTED_ISSUES]:
        if not isinstance(issue, dict):
            continue
        path = ".".join(str(part) for part in issue.get("path") or ()) or "body"
        message = issue.get("message")
        reported.append(
            f"{path}: {message}" if isinstance(message, str) and message else path
        )
    if not reported:
        return None
    if len(issues) > len(reported):
        reported.append(f"and {len(issues) - len(reported)} more")
    return "; ".join(reported)


def _loads(value: Any) -> Any:
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None
