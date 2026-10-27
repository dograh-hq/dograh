"""Immediate campaign pauses are limited to provider HTTP 401, 402 and 403."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import ErrorFrame
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.utils.errors import ErrorCategory

from api.errors.failure import ErrorSource
from api.services.campaign import circuit_breaker as module
from api.services.campaign.circuit_breaker import CircuitBreaker


class ProviderError(Exception):
    def __init__(self, status):
        self.status_code = status
        super().__init__("Provider rejected request")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 429, 500, 503])
async def test_only_requested_http_statuses_pause(status, monkeypatch):
    pause = AsyncMock(return_value=8)
    monkeypatch.setattr(module.db_client, "pause_campaign_for_provider_error", pause)
    await CircuitBreaker().pause_on_provider_error(
        organization_id=1,
        workflow_run_id=7,
        provider="deepgram",
        service="stt",
        exception=ProviderError(status),
    )
    if status in {401, 402, 403}:
        pause.assert_awaited_once_with(
            1, 7, provider="deepgram", service="stt", status_code=status
        )
    else:
        pause.assert_not_awaited()


@pytest.mark.parametrize(
    "message, expected",
    [
        ("server rejected WebSocket connection: HTTP 402", 402),
        ("Error code: 401 - unauthorized", 401),
        ("provider status_code=403", 403),
        ("received HTTP 500 upstream, then HTTP 401", 401),
        ("HTTP 429 retry failed: HTTP 402", 402),
        ("audio chunk 402 failed", None),
        ("quota exhausted", None),
    ],
)
def test_protocol_status_from_string_only_errors(message, expected):
    assert module.provider_http_status(message=message) == expected


def test_structured_status_takes_precedence_over_nested_message():
    assert module.provider_http_status(ProviderError(500), "previous HTTP 402") == 500
    error = RuntimeError("rejected")
    error.response = SimpleNamespace(status=403)  # aiohttp-style response
    assert module.provider_http_status(error) == 403


@pytest.mark.asyncio
async def test_service_hook_captures_run_and_persists_before_propagation(monkeypatch):
    monkeypatch.setattr(module, "get_current_run_id", lambda: "7")
    monkeypatch.setattr(module, "get_current_org_id", lambda: "1")
    events = []
    pause = AsyncMock(side_effect=lambda *args, **kwargs: events.append("paused"))
    monkeypatch.setattr(module.db_client, "pause_campaign_for_provider_error", pause)
    service = FrameProcessor()
    service.push_frame = AsyncMock(
        side_effect=lambda *args, **kwargs: events.append("propagated")
    )
    module.register_campaign_provider_error_handler(
        service, provider="deepgram", source=ErrorSource.STT
    )
    monkeypatch.setattr(module, "get_current_run_id", lambda: "another run")
    await service.push_error_frame(ErrorFrame(error="HTTP 402"))
    assert events == ["paused", "propagated"]
    pause.assert_awaited_once_with(
        1, 7, provider="deepgram", service="stt", status_code=402
    )


@pytest.mark.asyncio
async def test_application_errors_and_non_http_quota_do_not_pause(monkeypatch):
    monkeypatch.setattr(module, "get_current_run_id", lambda: "7")
    monkeypatch.setattr(module, "get_current_org_id", lambda: "1")
    pause = AsyncMock()
    monkeypatch.setattr(module.db_client, "pause_campaign_for_provider_error", pause)
    service = FrameProcessor()
    service.push_frame = AsyncMock()
    module.register_campaign_provider_error_handler(
        service, provider="openai", source=ErrorSource.LLM
    )
    await service.push_error_frame(
        ErrorFrame(error="HTTP 401", category=ErrorCategory.APPLICATION)
    )
    await service.push_error_frame(
        ErrorFrame(error="quota exhausted", category=ErrorCategory.QUOTA)
    )
    pause.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_factories_cover_stt_tts_and_fallback_children(monkeypatch):
    from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
    from api.services.configuration.model_connections import _build_service
    from api.services.pipecat import service_factory
    from api.services.pipecat.audio_config import AudioConfig

    monkeypatch.setattr(module, "get_current_run_id", lambda: "7")
    monkeypatch.setattr(module, "get_current_org_id", lambda: "1")
    pause = AsyncMock()
    monkeypatch.setattr(module.db_client, "pause_campaign_for_provider_error", pause)

    def selection(role, provider):
        return _build_service(role, provider, {"api_key": "test-key"}, {}, {})

    config = EffectiveAIModelConfiguration(
        llm=selection("llm", "openai"),
        stt=selection("stt", "deepgram"),
        tts=selection("tts", "deepgram"),
        llm_fallback={
            "version": 1,
            "rules": [
                {"condition": {"type": "error"}, "target": selection("llm", "openai")}
            ],
        },
    )
    audio = AudioConfig(16000, 16000)
    stt = service_factory.create_stt_service(config, audio)
    tts = service_factory.create_tts_service(config, audio)
    llm = service_factory.create_llm_service(config)
    for processor in [stt, tts, *llm._services]:
        await processor._call_event_handler("on_error", ErrorFrame(error="HTTP 402"))
    assert pause.await_count == 4
    assert {call.kwargs["service"] for call in pause.await_args_list} == {
        "stt",
        "tts",
        "llm",
    }
    assert all(call.args == (1, 7) for call in pause.await_args_list)


@pytest.mark.asyncio
async def test_resume_clears_old_ratio_before_readmitting_dispatch(monkeypatch):
    from api.services.campaign import runner

    events = []
    monkeypatch.setattr(
        runner.db_client,
        "get_campaign_by_id",
        AsyncMock(return_value=SimpleNamespace(state="paused", organization_id=1)),
    )
    monkeypatch.setattr(
        runner.circuit_breaker,
        "reset",
        AsyncMock(side_effect=lambda *_: events.append("reset") or True),
    )

    @asynccontextmanager
    async def transition(campaign_id, organization_id):
        assert (campaign_id, organization_id) == (8, 1)
        events.append("locked")
        yield
        events.append("running")

    monkeypatch.setattr(
        runner.db_client,
        "campaign_resume_transition",
        transition,
    )
    await runner.campaign_runner_service.resume_campaign(8)
    assert events == ["locked", "reset", "running"]
