import sys
from types import ModuleType
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest
from fastapi import HTTPException

from api.services.telephony.providers.papi_voip.provider import PapiVoipProvider


class _FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def text(self):
        return str(self._payload)

    async def json(self):
        return self._payload


class _FakeWebSocketContext:
    def __init__(self, websocket):
        self.websocket = websocket

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _FakeWebSocket:
    async def receive(self):
        return aiohttp.WSMessage(aiohttp.WSMsgType.BINARY, b"pcm", "")


class _FakeSession:
    def __init__(self, *, post_response=None, websocket=None, delete_response=None):
        self.post_response = post_response
        self.websocket = websocket
        self.delete_response = delete_response or _FakeResponse(204, {})
        self.post_calls = []
        self.ws_connect_calls = []
        self.delete_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def post(self, endpoint, **kwargs):
        self.post_calls.append((endpoint, kwargs))
        return self.post_response

    def ws_connect(self, endpoint, **kwargs):
        self.ws_connect_calls.append((endpoint, kwargs))
        return _FakeWebSocketContext(self.websocket)

    def delete(self, endpoint, **kwargs):
        self.delete_calls.append((endpoint, kwargs))
        return self.delete_response


class _FailingMediaSession(_FakeSession):
    def ws_connect(self, endpoint, **kwargs):
        self.ws_connect_calls.append((endpoint, kwargs))
        raise aiohttp.ClientConnectionError("PAPI media socket unavailable")


def _provider():
    return PapiVoipProvider(
        {
            "provider": "papi_voip",
            "base_url": "https://api.papi.api.br",
            "api_key": "instance-api-key",
            "instance_id": "instance-123",
            "from_numbers": ["5511999999999"],
        }
    )


@pytest.mark.asyncio
async def test_initiate_call_schedules_outbound_media_stream_after_dial():
    provider = _provider()
    session = _FakeSession(
        post_response=_FakeResponse(200, {"call_id": "call-123", "status": "ringing"})
    )
    provider._schedule_media_stream_task = Mock()

    with (
        patch(
            "api.services.telephony.providers.papi_voip.provider.aiohttp.ClientSession",
            return_value=session,
        ),
    ):
        result = await provider.initiate_call(
            to_number="+5511988887777",
            webhook_url="https://api.example.com/webhook",
            workflow_run_id=42,
            workflow_id=7,
            organization_id=9,
        )

    assert result.call_id == "call-123"
    provider._schedule_media_stream_task.assert_called_once_with(
        workflow_id=7,
        organization_id=9,
        workflow_run_id=42,
        call_id="call-123",
    )


@pytest.mark.asyncio
async def test_initiate_call_uses_nested_papi_call_id_for_media_stream():
    provider = _provider()
    session = _FakeSession(
        post_response=_FakeResponse(
            200,
            {"call": {"id": "papi-call-123"}, "status": "ringing"},
        )
    )
    provider._schedule_media_stream_task = Mock()

    with patch(
        "api.services.telephony.providers.papi_voip.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await provider.initiate_call(
            to_number="+5511988887777",
            webhook_url="https://api.example.com/webhook",
            workflow_run_id=42,
            workflow_id=7,
            organization_id=9,
        )

    assert result.call_id == "papi-call-123"
    provider._schedule_media_stream_task.assert_called_once_with(
        workflow_id=7,
        organization_id=9,
        workflow_run_id=42,
        call_id="papi-call-123",
    )


@pytest.mark.asyncio
async def test_initiate_call_raises_when_call_id_missing():
    provider = _provider()
    session = _FakeSession(post_response=_FakeResponse(200, {"status": "initiated"}))
    provider._schedule_media_stream_task = Mock()

    with patch(
        "api.services.telephony.providers.papi_voip.provider.aiohttp.ClientSession",
        return_value=session,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await provider.initiate_call(
                to_number="+5511988887777",
                webhook_url="https://api.example.com/webhook",
                workflow_run_id=42,
                workflow_id=7,
                organization_id=9,
            )

    assert exc_info.value.status_code == 502
    assert "call ID" in exc_info.value.detail
    provider._schedule_media_stream_task.assert_not_called()


@pytest.mark.asyncio
async def test_connect_outbound_media_stream_uses_papi_call_stream_endpoint():
    provider = _provider()
    session = _FakeSession(websocket=_FakeWebSocket())
    run_pipeline = AsyncMock()
    pipeline_module = ModuleType("api.services.pipecat.run_pipeline")
    pipeline_module.run_pipeline_telephony = run_pipeline

    with (
        patch(
            "api.services.telephony.providers.papi_voip.provider.aiohttp.ClientSession",
            return_value=session,
        ),
        patch.dict(sys.modules, {"api.services.pipecat.run_pipeline": pipeline_module}),
    ):
        await provider._connect_outbound_media_stream(
            workflow_id=7,
            organization_id=9,
            workflow_run_id=42,
            call_id="call-123",
        )

    assert session.ws_connect_calls == [
        (
            "https://api.papi.api.br/api/instances/instance-123/voice/calls/call-123/stream",
            {
                "headers": {
                    "x-api-key": "instance-api-key",
                    "apikey": "instance-api-key",
                },
                "heartbeat": 30,
                "timeout": None,
            },
        )
    ]
    run_pipeline.assert_awaited_once()
    assert run_pipeline.await_args.kwargs["provider_name"] == "papi_voip"
    assert run_pipeline.await_args.kwargs["workflow_id"] == 7
    assert run_pipeline.await_args.kwargs["organization_id"] == 9
    assert run_pipeline.await_args.kwargs["workflow_run_id"] == 42
    assert run_pipeline.await_args.kwargs["call_id"] == "call-123"
    assert run_pipeline.await_args.kwargs["transport_kwargs"] == {"call_id": "call-123"}


@pytest.mark.asyncio
async def test_connect_outbound_media_stream_hangs_up_known_call_after_retry_exhaustion():
    provider = _provider()
    session = _FailingMediaSession()
    mark_failed = AsyncMock()
    failure_module = ModuleType("api.services.workflow_run_failure")
    failure_module.mark_workflow_run_failed = mark_failed
    concurrency_module = ModuleType("api.services.call_concurrency")
    concurrency_module.call_concurrency = Mock(
        release_workflow_run_slot=AsyncMock(),
    )

    with (
        patch(
            "api.services.telephony.providers.papi_voip.provider.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "api.services.telephony.providers.papi_voip.provider.PAPI_MEDIA_STREAM_CONNECT_TIMEOUT_SECS",
            0,
        ),
        patch.dict(
            sys.modules,
            {
                "api.services.workflow_run_failure": failure_module,
                "api.services.call_concurrency": concurrency_module,
            },
        ),
    ):
        await provider._connect_outbound_media_stream(
            workflow_id=7,
            organization_id=9,
            workflow_run_id=42,
            call_id="call-123",
        )

    assert session.delete_calls == [
        (
            "https://api.papi.api.br/api/instances/instance-123/voice/calls/call-123",
            {"headers": {"x-api-key": "instance-api-key", "apikey": "instance-api-key"}},
        )
    ]
    mark_failed.assert_awaited_once_with(
        42,
        "Papi Voip media stream could not be established",
    )
    concurrency_module.call_concurrency.release_workflow_run_slot.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_media_failure_keeps_concurrency_slot_when_papi_hangup_is_not_confirmed():
    provider = _provider()
    session = _FailingMediaSession(delete_response=_FakeResponse(500, {"error": "unavailable"}))
    mark_failed = AsyncMock()
    failure_module = ModuleType("api.services.workflow_run_failure")
    failure_module.mark_workflow_run_failed = mark_failed
    concurrency_module = ModuleType("api.services.call_concurrency")
    concurrency_module.call_concurrency = Mock(
        release_workflow_run_slot=AsyncMock(),
    )

    with (
        patch(
            "api.services.telephony.providers.papi_voip.provider.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "api.services.telephony.providers.papi_voip.provider.PAPI_MEDIA_STREAM_CONNECT_TIMEOUT_SECS",
            0,
        ),
        patch.dict(
            sys.modules,
            {
                "api.services.workflow_run_failure": failure_module,
                "api.services.call_concurrency": concurrency_module,
            },
        ),
    ):
        await provider._connect_outbound_media_stream(
            workflow_id=7,
            organization_id=9,
            workflow_run_id=42,
            call_id="call-123",
        )

    mark_failed.assert_awaited_once_with(
        42,
        "Papi Voip media stream could not be established",
    )
    concurrency_module.call_concurrency.release_workflow_run_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_unsigned_callbacks():
    provider = _provider()

    assert not await provider.verify_inbound_signature(
        "https://dograh.example/api/v1/telephony/inbound/run",
        {"provider": "papi_voip"},
        {},
    )
