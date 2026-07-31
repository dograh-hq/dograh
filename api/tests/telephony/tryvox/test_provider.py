import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.db import db_client
from api.enums import TelephonyCallStatus
from api.errors.telephony_errors import TelephonyError
from api.services.telephony.providers.tryvox.provider import TryVoxProvider


def _provider(**overrides) -> TryVoxProvider:
    config = {
        "auth_id": "TJaccount",
        "auth_token": "account-token",
        "webhook_secret": "account-webhook-secret",
        "application_id": "app_123",
        "api_base_url": "https://api.tryvox.test",
        "from_numbers": ["+15551230001"],
    }
    config.update(overrides)
    return TryVoxProvider(config)


def _signed_headers(body: str, *, timestamp: int | None = None) -> dict[str, str]:
    timestamp = timestamp or int(time.time())
    digest = hmac.new(
        b"account-webhook-secret",
        f"{timestamp}.{body}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "x-tryvox-timestamp": str(timestamp),
        "x-tryvox-signature": f"t={timestamp},v1={digest}",
    }


class _Response:
    def __init__(self, status: int, data: dict):
        self.status = status
        self.data = data

    async def json(self):
        return self.data

    async def text(self):
        return json.dumps(self.data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Session:
    def __init__(self, response: _Response):
        self.response = response
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append(("POST", url, kwargs))
        return self.response

    def get(self, url, **kwargs):
        self.requests.append(("GET", url, kwargs))
        return self.response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SequenceSession:
    def __init__(self, responses: list[_Response]):
        self.responses = responses
        self.requests = []

    def patch(self, url, **kwargs):
        self.requests.append(("PATCH", url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        self.requests.append(("POST", url, kwargs))
        return self.responses.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _WebSocket:
    def __init__(self, metadata: dict):
        self.metadata = metadata
        self.closed = None

    async def receive_text(self):
        return json.dumps(self.metadata)

    async def close(self, code: int, reason: str):
        self.closed = (code, reason)


@pytest.mark.asyncio
async def test_initiate_call_uses_tryvox_voice_api_and_signed_callbacks():
    provider = _provider()
    session = _Session(
        _Response(
            201,
            {
                "data": {
                    "request_uuid": "call-123",
                    "status": "queued",
                }
            },
        )
    )

    with (
        patch(
            "api.services.telephony.providers.tryvox.provider.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "api.services.telephony.providers.tryvox.provider.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://dograh.test", "wss://dograh.test"),
        ),
    ):
        result = await provider.initiate_call(
            "+15551230002",
            "https://dograh.test/api/v1/telephony/tryvox/answer?workflow_run_id=9",
            workflow_run_id=9,
        )

    assert result.call_id == "call-123"
    assert result.caller_number == "+15551230001"
    method, url, kwargs = session.requests[0]
    assert method == "POST"
    assert url == "https://api.tryvox.test/v1/voice/accounts/TJaccount/calls"
    assert kwargs["json"] == {
        "from": "+15551230001",
        "to": "+15551230002",
        "answer_url": (
            "https://dograh.test/api/v1/telephony/tryvox/answer?workflow_run_id=9"
        ),
        "answer_method": "POST",
        "webhook_secret": "account-webhook-secret",
        "status_callback_url": ("https://dograh.test/api/v1/telephony/tryvox/status/9"),
        "status_callback_method": "POST",
    }


@pytest.mark.asyncio
async def test_verify_inbound_signature_accepts_exact_raw_body():
    provider = _provider()
    body = '{"call_uuid":"call-123","account_id":"TJaccount"}'
    headers = _signed_headers(body)

    assert await provider.verify_inbound_signature(
        "https://dograh.test/answer", json.loads(body), headers, body
    )


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_tampering_and_replay():
    provider = _provider()
    body = '{"call_uuid":"call-123","account_id":"TJaccount"}'
    headers = _signed_headers(body)
    stale_headers = _signed_headers(body, timestamp=int(time.time()) - 301)

    assert not await provider.verify_inbound_signature(
        "https://dograh.test/answer",
        json.loads(body),
        headers,
        body.replace("call-123", "call-999"),
    )
    assert not await provider.verify_inbound_signature(
        "https://dograh.test/answer",
        json.loads(body),
        stale_headers,
        body,
    )


@pytest.mark.asyncio
async def test_get_webhook_response_is_native_voxml_stream():
    provider = _provider()
    with (
        patch(
            "api.services.telephony.providers.tryvox.provider.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://dograh.test", "wss://dograh.test"),
        ),
        patch(
            "api.services.telephony.providers.tryvox.provider."
            "tryvox_security.issue_stream_token",
            new_callable=AsyncMock,
            return_value="stream-token",
        ),
    ):
        response = json.loads(await provider.get_webhook_response(7, 11, 13))

    assert response == {
        "voxml_version": "1.0",
        "instructions": [
            {
                "verb": "Stream",
                "url": (
                    "wss://dograh.test/api/v1/telephony/tryvox/ws/7/11/13"
                    "?token=stream-token"
                ),
                "track": "inbound_track",
                "parameters": {
                    "provider": "tryvox",
                    "workflow_run_id": "13",
                },
            }
        ],
    }


@pytest.mark.asyncio
async def test_inbound_stream_uses_tryvox_subprotocol_route():
    provider = _provider()
    with patch(
        "api.services.telephony.providers.tryvox.provider."
        "tryvox_security.issue_stream_token",
        new_callable=AsyncMock,
        return_value="stream-token",
    ):
        response = await provider.start_inbound_stream(
            websocket_url="wss://dograh.test/api/v1/telephony/ws/7/11/13",
            workflow_run_id=13,
            normalized_data=SimpleNamespace(),
            backend_endpoint="https://dograh.test",
        )

    payload = json.loads(response.body)
    assert payload["instructions"][0]["url"] == (
        "wss://dograh.test/api/v1/telephony/tryvox/ws/7/11/13?token=stream-token"
    )


def test_parse_inbound_and_status_payloads():
    provider = _provider()
    inbound = provider.parse_inbound_webhook(
        {
            "call_uuid": "call-123",
            "account_id": "TJaccount",
            "from": "+15551230002",
            "to": "+15551230001",
            "direction": "inbound",
            "status": "answered",
        }
    )
    status = provider.parse_status_callback(
        {
            "CallUUID": "call-123",
            "From": "+15551230001",
            "To": "+15551230002",
            "Direction": "outbound",
            "Status": "hangup",
            "Duration": 12,
        }
    )

    assert inbound.provider == "tryvox"
    assert inbound.account_id == "TJaccount"
    assert inbound.call_id == "call-123"
    assert status["call_id"] == "call-123"
    assert status["duration"] == "12"
    assert status["status"] == TelephonyCallStatus.COMPLETED


@pytest.mark.asyncio
async def test_configure_inbound_updates_application_then_assigns_number():
    provider = _provider()
    session = _SequenceSession([_Response(200, {}), _Response(200, {})])

    with patch(
        "api.services.telephony.providers.tryvox.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await provider.configure_inbound(
            "+15551230001",
            "https://dograh.test/api/v1/telephony/inbound/run",
        )

    assert result.ok is True
    assert session.requests == [
        (
            "PATCH",
            "https://api.tryvox.test/v1/voice/applications/app_123",
            {
                "json": {
                    "answer_url": ("https://dograh.test/api/v1/telephony/inbound/run"),
                    "answer_method": "POST",
                    "webhook_secret": "account-webhook-secret",
                },
                "auth": provider._auth(),
            },
        ),
        (
            "POST",
            (
                "https://api.tryvox.test/v1/account/TJaccount/numbers/"
                "+15551230001/application"
            ),
            {
                "json": {"application_id": "app_123"},
                "auth": provider._auth(),
            },
        ),
    ]


@pytest.mark.asyncio
async def test_websocket_metadata_starts_pipeline_with_stored_call_id():
    provider = _provider()
    websocket = _WebSocket({"provider": "tryvox", "workflow_run_id": "13"})
    workflow_run = SimpleNamespace(gathered_context={"call_id": "call-123"})

    with (
        patch.object(
            db_client,
            "get_workflow_run",
            new_callable=AsyncMock,
            return_value=workflow_run,
        ),
        patch(
            "api.services.pipecat.run_pipeline.run_pipeline_telephony",
            new_callable=AsyncMock,
        ) as run_pipeline,
    ):
        await provider.handle_websocket(websocket, 7, 11, 13)

    assert websocket.closed is None
    run_pipeline.assert_awaited_once_with(
        websocket,
        provider_name="tryvox",
        workflow_id=7,
        workflow_run_id=13,
        organization_id=11,
        call_id="call-123",
        transport_kwargs={"call_id": "call-123"},
    )


@pytest.mark.asyncio
async def test_websocket_rejects_wrong_run_metadata():
    provider = _provider()
    websocket = _WebSocket({"workflow_run_id": "99"})
    workflow_run = SimpleNamespace(gathered_context={"call_id": "call-123"})

    with patch.object(
        db_client,
        "get_workflow_run",
        new_callable=AsyncMock,
        return_value=workflow_run,
    ):
        await provider.handle_websocket(websocket, 7, 11, 13)

    assert websocket.closed == (4403, "Stream metadata mismatch")


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [{}, [], {"provider": "tryvox"}])
async def test_websocket_requires_object_metadata_with_exact_run_id(metadata):
    provider = _provider()
    websocket = _WebSocket(metadata)
    workflow_run = SimpleNamespace(gathered_context={"call_id": "call-123"})

    with patch.object(
        db_client,
        "get_workflow_run",
        new_callable=AsyncMock,
        return_value=workflow_run,
    ):
        await provider.handle_websocket(websocket, 7, 11, 13)

    assert websocket.closed == (4403, "Stream metadata mismatch")


def test_validation_error_response_uses_voxml_json():
    response = TryVoxProvider.generate_validation_error_response(
        TelephonyError.PHONE_NUMBER_NOT_CONFIGURED
    )
    payload = json.loads(response.body)

    assert payload["voxml_version"] == "1.0"
    assert payload["instructions"][-1] == {"verb": "Hangup"}
