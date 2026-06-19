"""Regression tests for Cloudonix CDR webhook handling.

A Cloudonix CDR webhook is a public, unauthenticated endpoint that parses
arbitrary external JSON. A partial / malformed payload (missing ``session``,
or a ``null`` ``session`` / ``disposition``) must produce a graceful error
response, not an unhandled ``AttributeError`` (HTTP 500).
"""

from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from api.services.telephony.providers.cloudonix.routes import handle_cloudonix_cdr
from api.services.telephony.status_processor import StatusCallbackRequest


def _json_request(body: bytes) -> Request:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("example.test", 443),
            "path": "/api/v1/telephony/cloudonix/cdr",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


@pytest.mark.asyncio
async def test_cdr_route_handles_payload_without_session():
    """A CDR payload missing the ``session`` object returns a graceful error
    instead of raising ``AttributeError`` on ``None.get("token")``."""
    request = _json_request(b'{"domain": "acme.cloudonix.io", "disposition": "ANSWER"}')

    with patch(
        "api.services.telephony.providers.cloudonix.routes.db_client"
    ) as db_client:
        db_client.get_workflow_run_by_call_id = AsyncMock(return_value=None)

        result = await handle_cloudonix_cdr(request)

    assert result == {"status": "error", "message": "Missing call_id field"}


@pytest.mark.asyncio
async def test_cdr_route_handles_null_session():
    """A CDR payload with an explicit ``null`` session is handled gracefully."""
    request = _json_request(b'{"domain": "acme.cloudonix.io", "session": null}')

    with patch(
        "api.services.telephony.providers.cloudonix.routes.db_client"
    ) as db_client:
        db_client.get_workflow_run_by_call_id = AsyncMock(return_value=None)

        result = await handle_cloudonix_cdr(request)

    assert result == {"status": "error", "message": "Missing call_id field"}


@pytest.mark.asyncio
async def test_cdr_route_handles_string_session():
    """A CDR payload with a non-object session is handled gracefully."""
    request = _json_request(b'{"domain": "acme.cloudonix.io", "session": "abc"}')

    with patch(
        "api.services.telephony.providers.cloudonix.routes.db_client"
    ) as db_client:
        db_client.get_workflow_run_by_call_id = AsyncMock(return_value=None)

        result = await handle_cloudonix_cdr(request)

    assert result == {"status": "error", "message": "Missing call_id field"}


def test_from_cloudonix_cdr_tolerates_missing_session_and_disposition():
    """``from_cloudonix_cdr`` must not crash on a partial CDR payload."""
    # Missing both session and disposition.
    req = StatusCallbackRequest.from_cloudonix_cdr({"domain": "acme.cloudonix.io"})
    assert req.call_id == ""
    assert req.status == ""

    # Explicit null values.
    req = StatusCallbackRequest.from_cloudonix_cdr(
        {"session": None, "disposition": None}
    )
    assert req.call_id == ""
    assert req.status == ""


def test_from_cloudonix_cdr_tolerates_string_session():
    """``from_cloudonix_cdr`` treats a non-object session as missing call_id."""
    req = StatusCallbackRequest.from_cloudonix_cdr(
        {"session": "abc", "disposition": "ANSWER"}
    )
    assert req.call_id == ""
    assert req.status == "completed"


def test_from_cloudonix_cdr_maps_disposition_and_session_token():
    """Normal, well-formed CDR payloads still map correctly."""
    req = StatusCallbackRequest.from_cloudonix_cdr(
        {
            "session": {"token": "abc123"},
            "disposition": "BUSY",
            "from": "+15551230001",
            "to": "+15551230002",
            "billsec": 12,
        }
    )
    assert req.call_id == "abc123"
    assert req.status == "busy"
    assert req.duration == "12"
