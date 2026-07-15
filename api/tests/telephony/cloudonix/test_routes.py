"""Regression tests for Cloudonix CDR webhook handling.

A Cloudonix CDR webhook is a public, unauthenticated endpoint that parses
arbitrary external JSON. A partial / malformed payload (missing ``session``,
or a ``null`` ``session`` / ``disposition``) must produce a graceful error
response, not an unhandled ``AttributeError`` (HTTP 500).
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from api.enums import TelephonyCallStatus
from api.services.telephony.providers.cloudonix.provider import CloudonixProvider
from api.services.telephony.providers.cloudonix.routes import handle_cloudonix_cdr


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


class _FakeWebSocket:
    def __init__(self, *messages: str):
        self.receive_text = AsyncMock(side_effect=messages)
        self.close = AsyncMock()


@pytest.mark.asyncio
async def test_agent_stream_reads_call_metadata_from_start_message():
    """Cloudonix agent-stream uses start metadata, not call query params."""
    provider = CloudonixProvider({})
    websocket = _FakeWebSocket(
        json.dumps({"event": "connected"}),
        json.dumps(
            {
                "event": "start",
                "start": {
                    "streamSid": "stream-123",
                    "callSid": "call-123",
                    "session": "session-123",
                    "accountSid": "acme",
                    "from": "+15551230001",
                    "to": "+15551230002",
                    "context": "inbound",
                    "tracks": ["inbound"],
                    "mediaFormat": {
                        "encoding": "audio/x-mulaw",
                        "sampleRate": 8000,
                        "channels": 1,
                    },
                },
            }
        ),
    )
    config = SimpleNamespace(
        id=7,
        credentials={
            "domain_id": "acme.cloudonix.net",
            "bearer_token": "secret-token",
        },
    )
    provider._find_config_by_domain = AsyncMock(return_value=config)
    provider._validate_session = AsyncMock(return_value=True)

    with (
        patch(
            "api.services.telephony.providers.cloudonix.provider.db_client"
        ) as db_client,
        patch(
            "api.services.pipecat.run_pipeline.run_pipeline_telephony",
            new_callable=AsyncMock,
        ) as run_pipeline,
    ):
        db_client.update_workflow_run = AsyncMock()

        await provider.handle_external_websocket(
            websocket,
            organization_id=44,
            workflow_id=101,
            workflow_run_id=303,
            params={},
        )

    provider._find_config_by_domain.assert_awaited_once_with(44, "acme.cloudonix.net")
    provider._validate_session.assert_awaited_once_with(
        "acme.cloudonix.net", "session-123", "secret-token"
    )
    db_client.update_workflow_run.assert_awaited_once_with(
        run_id=303,
        initial_context={
            "caller_number": "+15551230001",
            "called_number": "+15551230002",
            "direction": "inbound",
            "cloudonix_context": "inbound",
        },
        gathered_context={
            "call_id": "session-123",
            "cloudonix_call_sid": "call-123",
            "cloudonix_stream_sid": "stream-123",
        },
        logs={
            "inbound_webhook": {
                "domain": "acme.cloudonix.net",
                "session": "session-123",
                "callSid": "call-123",
                "streamSid": "stream-123",
                "from": "+15551230001",
                "to": "+15551230002",
                "context": "inbound",
                "tracks": ["inbound"],
                "mediaFormat": {
                    "encoding": "audio/x-mulaw",
                    "sampleRate": 8000,
                    "channels": 1,
                },
            },
        },
    )
    run_pipeline.assert_awaited_once()
    _, kwargs = run_pipeline.await_args
    assert kwargs["call_id"] == "session-123"
    assert kwargs["transport_kwargs"] == {
        "call_id": "session-123",
        "stream_sid": "stream-123",
        "bearer_token": "secret-token",
        "domain_id": "acme.cloudonix.net",
    }
    websocket.close.assert_not_awaited()


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


def test_parse_cloudonix_cdr_tolerates_missing_session_and_disposition():
    """Cloudonix CDR parsing must not crash on a partial payload."""
    # Missing both session and disposition.
    req = CloudonixProvider.parse_cdr_status_callback({"domain": "acme.cloudonix.io"})
    assert req["call_id"] == ""
    assert req["status"] == ""

    # Explicit null values.
    req = CloudonixProvider.parse_cdr_status_callback(
        {"session": None, "disposition": None}
    )
    assert req["call_id"] == ""
    assert req["status"] == ""


def test_parse_cloudonix_cdr_tolerates_string_session():
    """Cloudonix CDR parsing treats a non-object session as missing call_id."""
    req = CloudonixProvider.parse_cdr_status_callback(
        {"session": "abc", "disposition": "ANSWER"}
    )
    assert req["call_id"] == ""
    assert req["status"] == TelephonyCallStatus.COMPLETED


def test_parse_cloudonix_cdr_maps_disposition_and_session_token():
    """Normal, well-formed CDR payloads still map correctly."""
    req = CloudonixProvider.parse_cdr_status_callback(
        {
            "session": {"token": "abc123"},
            "disposition": "BUSY",
            "from": "+15551230001",
            "to": "+15551230002",
            "billsec": 12,
        }
    )
    assert req["call_id"] == "abc123"
    assert req["status"] == TelephonyCallStatus.BUSY
    assert req["duration"] == "12"


def test_parse_cloudonix_cdr_preserves_zero_billsec():
    """A zero billed duration must not fall back to total call duration."""
    req = CloudonixProvider.parse_cdr_status_callback(
        {
            "session": {"token": "abc123"},
            "disposition": "ANSWER",
            "billsec": 0,
            "duration": 42,
        }
    )

    assert req["duration"] == "0"


@pytest.mark.asyncio
async def test_agent_stream_handshake_timeout_closes_socket(monkeypatch):
    """An idle agent-stream socket holds an org concurrency slot, so the
    handshake read must be bounded rather than waiting forever."""
    import asyncio

    from api.services.telephony.providers.cloudonix import provider as provider_module

    monkeypatch.setattr(provider_module, "AGENT_STREAM_HANDSHAKE_TIMEOUT_S", 0.05)
    provider = CloudonixProvider({})

    async def never_returns():
        await asyncio.Event().wait()

    websocket = SimpleNamespace(receive_text=never_returns, close=AsyncMock())

    await asyncio.wait_for(
        provider.handle_external_websocket(
            websocket,
            organization_id=1,
            workflow_id=2,
            workflow_run_id=4,
            params={},
        ),
        timeout=5,
    )

    websocket.close.assert_awaited_once_with(code=4408, reason="Handshake timeout")
