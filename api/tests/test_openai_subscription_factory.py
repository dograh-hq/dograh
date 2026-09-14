from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData
from pipecat.processors.frame_processor import FrameDirection
from pipecat.turns.user_start import ExternalUserTurnStartStrategy
from pipecat.turns.user_stop import ExternalUserTurnStopStrategy

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.openai_subscription_auth import SubscriptionAuthError
from api.services.pipecat.pipeline_metrics_aggregator import PipelineMetricsAggregator
from api.services.pipecat.run_pipeline import _create_realtime_user_turn_config
from api.services.pipecat.service_factory import create_realtime_llm_service
from api.services.pipecat.usage_metrics import SubscriptionVoiceUsageMetricsData


def subscription_configuration():
    return EffectiveAIModelConfiguration.model_validate(
        {
            "is_realtime": True,
            "realtime": {
                "provider": "openai_live_subscription",
                "model": "gpt-live-1-codex",
                "api_key": "synthetic-backend-only",
                "backend_model": "gpt-5.4-mini",
            },
        }
    )


@pytest.mark.parametrize("organization_id", [None, 2])
def test_factory_rejects_unbound_organization_before_auth_read(
    monkeypatch, tmp_path, organization_id
):
    monkeypatch.setenv("DOGRAH_OPENAI_SUBSCRIPTION_ENABLED", "true")
    monkeypatch.setenv(
        "DOGRAH_OPENAI_SUBSCRIPTION_CODEX_HOME", str(tmp_path / "dedicated")
    )
    monkeypatch.setenv("DOGRAH_OPENAI_SUBSCRIPTION_ORGANIZATION_ID", "1")
    with pytest.raises(SubscriptionAuthError, match="organization"):
        create_realtime_llm_service(
            subscription_configuration(),
            SimpleNamespace(),
            organization_id=organization_id,
        )


def test_factory_default_disabled_without_login(monkeypatch):
    monkeypatch.delenv("DOGRAH_OPENAI_SUBSCRIPTION_ENABLED", raising=False)
    with pytest.raises(SubscriptionAuthError, match="disabled"):
        create_realtime_llm_service(
            subscription_configuration(), SimpleNamespace(), organization_id=1
        )


def test_factory_passes_backend_key_and_validated_organization(monkeypatch, tmp_path):
    import api.services.pipecat.realtime.openai_live_subscription as module

    monkeypatch.setenv("DOGRAH_OPENAI_SUBSCRIPTION_ENABLED", "true")
    monkeypatch.setenv(
        "DOGRAH_OPENAI_SUBSCRIPTION_CODEX_HOME", str(tmp_path / "dedicated")
    )
    monkeypatch.setenv("DOGRAH_OPENAI_SUBSCRIPTION_ORGANIZATION_ID", "1")
    constructor = Mock()
    monkeypatch.setattr(module, "DograhOpenAILiveSubscriptionLLMService", constructor)
    service = create_realtime_llm_service(
        subscription_configuration(), SimpleNamespace(), organization_id=1
    )
    assert service is constructor.return_value
    args = constructor.call_args.kwargs
    assert args["backend_api_key"] == "synthetic-backend-only"
    assert args["backend_model"] == "gpt-5.4-mini"
    assert args["organization_id"] == 1
    assert "api_key" not in args
    constructor.Settings.assert_called_once_with(model="gpt-live-1-codex", voice="cove")


def test_subscription_uses_continuous_live_turn_policy():
    strategies, vad = _create_realtime_user_turn_config(
        "openai_live_subscription", "gpt-live-1-codex"
    )
    assert vad is None
    assert isinstance(strategies.start[0], ExternalUserTurnStartStrategy)
    assert strategies.start[0]._enable_interruptions is False
    assert isinstance(strategies.stop[0], ExternalUserTurnStopStrategy)
    assert strategies.stop[0].wait_for_transcript is False


@pytest.mark.asyncio
async def test_subscription_usage_stays_separate_from_api_voice_and_backend_tokens():
    aggregator = PipelineMetricsAggregator()
    aggregator.push_frame = AsyncMock()
    aggregator._check_started = lambda _: True
    await aggregator.process_frame(
        MetricsFrame(
            data=[
                SubscriptionVoiceUsageMetricsData(
                    processor="SubscriptionVoice",
                    model="gpt-live-1-codex",
                    session_seconds=12.0,
                    input_audio_seconds=3.0,
                    output_audio_seconds=4.0,
                    session_state="ended",
                ),
                LLMUsageMetricsData(
                    processor="WorkflowBackend",
                    model="gpt-5.4-mini",
                    value=LLMTokenUsage(
                        prompt_tokens=8, completion_tokens=4, total_tokens=12
                    ),
                ),
            ]
        ),
        FrameDirection.DOWNSTREAM,
    )
    usage = aggregator.get_all_usage_metrics_serialized()
    assert "live_audio_seconds" not in usage
    voice = usage["subscription_voice"]["SubscriptionVoice|||gpt-live-1-codex"]
    assert voice == {
        "voice_auth": "subscription",
        "cost_usd": None,
        "session_state": "ended",
        "session_seconds": 12.0,
        "input_audio_seconds": 3.0,
        "output_audio_seconds": 4.0,
    }
    assert usage["llm"]["WorkflowBackend|||gpt-5.4-mini"]["total_tokens"] == 12
    aggregator.reset_metrics()
    assert "subscription_voice" not in aggregator.get_all_usage_metrics_serialized()
