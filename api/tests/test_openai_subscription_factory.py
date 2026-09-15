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
from api.services.pipecat.service_factory import (
    create_realtime_llm_service,
    create_subscription_inference_service,
)
from api.services.pipecat.usage_metrics import (
    SubscriptionReasoningUsageMetricsData,
    SubscriptionVoiceUsageMetricsData,
)


def subscription_configuration():
    return EffectiveAIModelConfiguration.model_validate(
        {
            "is_realtime": True,
            "realtime": {
                "provider": "openai_live_subscription",
                "model": "gpt-live-1-codex",
                "backend_model": "gpt-5.6-luna",
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


def test_factory_passes_subscription_model_and_validated_organization_without_key(
    monkeypatch, tmp_path
):
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
    assert "backend_api_key" not in args
    assert args["backend_model"] == "gpt-5.6-luna"
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
async def test_subscription_voice_and_reasoning_sum_separately_from_public_api_tokens():
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
                SubscriptionReasoningUsageMetricsData(
                    processor="SubscriptionReasoning",
                    model="gpt-5.6-luna",
                    value=LLMTokenUsage(
                        prompt_tokens=8,
                        completion_tokens=4,
                        total_tokens=12,
                        reasoning_tokens=2,
                    ),
                ),
                LLMUsageMetricsData(
                    processor="PublicAPIControl",
                    model="gpt-5.6-luna",
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
    public_key = "PublicAPIControl|||gpt-5.6-luna"
    subscription_key = "SubscriptionReasoning|||gpt-5.6-luna"
    assert set(usage["llm"]) == {public_key}
    assert usage["llm"][public_key]["total_tokens"] == 12
    assert (
        usage["subscription_reasoning"][subscription_key]["tokens"]["total_tokens"]
        == 12
    )
    await aggregator.process_frame(
        MetricsFrame(
            data=[
                SubscriptionReasoningUsageMetricsData(
                    processor="SubscriptionReasoning",
                    model="gpt-5.6-luna",
                    value=LLMTokenUsage(
                        prompt_tokens=5,
                        completion_tokens=2,
                        total_tokens=7,
                        cache_read_input_tokens=1,
                    ),
                ),
                SubscriptionVoiceUsageMetricsData(
                    processor="SubscriptionVoice",
                    model="gpt-live-1-codex",
                    session_seconds=6.0,
                    input_audio_seconds=1.0,
                    output_audio_seconds=2.0,
                    session_state="ended",
                ),
            ]
        ),
        FrameDirection.DOWNSTREAM,
    )
    usage = aggregator.get_all_usage_metrics_serialized()
    reasoning = usage["subscription_reasoning"][subscription_key]
    assert reasoning["reasoning_auth"] == "subscription"
    assert reasoning["cost_usd"] is None
    assert reasoning["tokens"] == LLMTokenUsage(
        prompt_tokens=13,
        completion_tokens=6,
        total_tokens=19,
        reasoning_tokens=2,
        cache_read_input_tokens=1,
    ).model_dump(mode="json")
    assert usage["subscription_voice"]["SubscriptionVoice|||gpt-live-1-codex"] == {
        **voice,
        "session_seconds": 18.0,
        "input_audio_seconds": 4.0,
        "output_audio_seconds": 6.0,
    }
    assert set(usage["llm"]) == {public_key}
    assert usage["llm"][public_key]["total_tokens"] == 12
    assert "live_audio_seconds" not in usage
    aggregator.reset_metrics()
    reset = aggregator.get_all_usage_metrics_serialized()
    assert "subscription_voice" not in reset
    assert "subscription_reasoning" not in reset
    assert reset["llm"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("model_override", [None, "custom-subscription-model"])
@pytest.mark.parametrize("ambient_api_key", [None, "synthetic-unused-ambient-key"])
async def test_standalone_inference_factory_owns_clients_without_using_an_api_key(
    monkeypatch, model_override, ambient_api_key
):
    import pipecat.services.openai.responses.llm as public_responses
    import redis.asyncio

    from api.services.configuration import openai_subscription_auth as auth_module
    from api.services.pipecat.realtime import openai_subscription_llm as llm_module

    if ambient_api_key is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", ambient_api_key)
    settings = object()
    monkeypatch.setattr(
        auth_module.SubscriptionAuthSettings, "from_env", lambda: settings
    )
    redis_client = AsyncMock()
    redis_factory = Mock(return_value=redis_client)
    monkeypatch.setattr(redis.asyncio.Redis, "from_url", redis_factory)
    auth = SimpleNamespace(assert_organization=Mock(), aclose=AsyncMock())
    auth_factory = Mock(return_value=auth)
    monkeypatch.setattr(auth_module, "SubscriptionAuthService", auth_factory)
    subscription_client = SimpleNamespace(aclose=AsyncMock())
    subscription_factory = Mock(return_value=subscription_client)
    monkeypatch.setattr(llm_module, "SubscriptionResponsesClient", subscription_factory)
    public_factory = Mock(
        side_effect=AssertionError("Public API client must not be created")
    )
    monkeypatch.setattr(public_responses, "AsyncOpenAI", public_factory)
    config = subscription_configuration()
    assert config.llm is None
    assert "api_key" not in config.realtime.model_dump()
    service = create_subscription_inference_service(
        config, organization_id=1, model_override=model_override
    )
    try:
        assert service._settings.model == (model_override or "gpt-5.6-luna")
        assert service._api_key is None
        assert service._client is None
        assert service._owns_auth_service is True
        assert service._auth_service is auth
        assert all(
            call.args == (1,) for call in auth.assert_organization.call_args_list
        )
        assert auth.assert_organization.call_count >= 1
        auth_factory.assert_called_once_with(
            settings, redis_client, owns_redis_client=True
        )
        subscription_factory.assert_called_once_with(auth, 1)
        public_factory.assert_not_called()
    finally:
        await service.aclose()
    subscription_client.aclose.assert_awaited_once()
    auth.aclose.assert_awaited_once()


@pytest.mark.parametrize(
    "config",
    [
        {"is_realtime": False},
        {"is_realtime": True},
        {
            "is_realtime": True,
            "realtime": {"provider": "openai_realtime", "api_key": "synthetic-key"},
        },
    ],
)
def test_standalone_inference_rejects_non_subscription_before_auth_or_redis(
    monkeypatch, config
):
    import redis.asyncio

    from api.services.configuration import openai_subscription_auth as auth_module

    auth_factory, redis_factory = Mock(), Mock()
    monkeypatch.setattr(auth_module, "SubscriptionAuthService", auth_factory)
    monkeypatch.setattr(redis.asyncio.Redis, "from_url", redis_factory)
    with pytest.raises(ValueError, match="subscription workflow provider"):
        create_subscription_inference_service(
            EffectiveAIModelConfiguration.model_validate(config), organization_id=1
        )
    auth_factory.assert_not_called()
    redis_factory.assert_not_called()


def test_standalone_inference_rejects_wrong_organization_before_client_creation(
    monkeypatch,
):
    import redis.asyncio

    from api.services.configuration import openai_subscription_auth as auth_module
    from api.services.pipecat.realtime import openai_subscription_llm as llm_module

    monkeypatch.setattr(
        auth_module.SubscriptionAuthSettings, "from_env", lambda: object()
    )
    monkeypatch.setattr(redis.asyncio.Redis, "from_url", Mock())
    assert_organization = Mock(
        side_effect=SubscriptionAuthError("organization_mismatch")
    )
    monkeypatch.setattr(
        auth_module,
        "SubscriptionAuthService",
        Mock(return_value=SimpleNamespace(assert_organization=assert_organization)),
    )
    constructor = Mock()
    monkeypatch.setattr(llm_module, "SubscriptionResponsesLLMService", constructor)
    with pytest.raises(SubscriptionAuthError, match="organization"):
        create_subscription_inference_service(
            subscription_configuration(), organization_id=2
        )
    assert_organization.assert_called_once_with(2)
    constructor.assert_not_called()


@pytest.mark.asyncio
async def test_inference_close_preserves_borrowed_auth_client(monkeypatch):
    from api.services.pipecat.realtime import openai_subscription_llm as llm_module

    auth = SimpleNamespace(assert_organization=Mock(), aclose=AsyncMock())
    subscription_client = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(
        llm_module,
        "SubscriptionResponsesClient",
        Mock(return_value=subscription_client),
    )
    service = llm_module.SubscriptionResponsesLLMService(
        auth_service=auth,
        organization_id=1,
        owns_auth_service=False,
        settings=llm_module.SubscriptionResponsesLLMService.Settings(
            model="gpt-5.6-luna"
        ),
    )
    await service.aclose()
    subscription_client.aclose.assert_awaited_once()
    auth.aclose.assert_not_awaited()
