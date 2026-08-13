import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    GANDR_TTS_MODELS,
    GANDR_TTS_VOICES,
    DograhLLMService,
    DograhSTTService,
    GandrTTSConfiguration,
    ServiceProviders,
)
from api.services.pipecat.service_factory import create_tts_service


def test_gandr_tts_configuration_defaults():
    config = GandrTTSConfiguration(api_key="test-key")

    assert config.provider == ServiceProviders.GANDR
    assert config.voice == "gandr-mia"
    assert config.model == "tts-1"
    assert config.base_url == "https://tts.gandr.ai/v1"
    assert GANDR_TTS_MODELS == ["tts-1"]
    assert "gandr-mia" in GANDR_TTS_VOICES


def test_create_gandr_tts_service_uses_http_base_url():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.GANDR.value,
            base_url="https://tts.gandr.ai/v1",
            api_key="gnd-test-key",
            model="tts-1",
            voice="gandr-ava",
            speed=1.0,
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )

    with patch(
        "api.services.pipecat.service_factory.SpeachesTTSService"
    ) as mock_service:
        create_tts_service(user_config, audio_config)

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["base_url"] == "https://tts.gandr.ai/v1"
    assert kwargs["api_key"] == "gnd-test-key"
    assert kwargs["settings"].model == "tts-1"
    assert kwargs["settings"].voice == "gandr-ava"
    assert kwargs["settings"].speed == 1.0


def test_create_gandr_tts_service_defaults_api_key_to_none_sentinel():
    # Mirrors the Speaches branch: a missing key is sent as "none" so the
    # OpenAI client does not error, matching the self-hosted (Speaches) pattern.
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.GANDR.value,
            base_url="https://tts.gandr.ai/v1",
            api_key=None,
            model="tts-1",
            voice="gandr-mia",
            speed=1.0,
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )

    with patch(
        "api.services.pipecat.service_factory.SpeachesTTSService"
    ) as mock_service:
        create_tts_service(user_config, audio_config)

    assert mock_service.call_args.kwargs["api_key"] == "none"


def test_gandr_is_registered_for_key_validation():
    validator = UserConfigurationValidator()
    assert ServiceProviders.GANDR.value in validator._validator_map


def _gandr_config(base_url=None):
    return SimpleNamespace(base_url=base_url) if base_url else SimpleNamespace()


def test_gandr_key_validation_accepts_valid_key():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        assert validator._check_gandr_api_key("tts-1", "gnd-valid-key") is True
    called_url = mock_get.call_args.args[0]
    assert called_url == "https://tts.gandr.ai/v1/voices"
    headers = mock_get.call_args.kwargs["headers"]
    assert headers["x-api-key"] == "gnd-valid-key"


def test_gandr_key_validation_uses_configured_base_url():
    # Save-time validation must match runtime synthesis: when a user points
    # base_url at a regional/proxy Gandr endpoint, the key check hits that
    # host's /voices, not the hard-coded default.
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        assert (
            validator._check_gandr_api_key(
                "tts-1",
                "gnd-valid-key",
                _gandr_config(base_url="https://tts-nyc.gandr.ai/v1"),
            )
            is True
        )
    called_url = mock_get.call_args.args[0]
    assert called_url == "https://tts-nyc.gandr.ai/v1/voices"


def test_gandr_key_validation_rejects_bad_key():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 401
        with pytest.raises(ValueError):
            validator._check_gandr_api_key("tts-1", "bad-key")


def test_gandr_key_validation_treats_network_error_as_loud():
    # A connection failure is not a verdict on the key: the validator raises a
    # clear error rather than silently accepting or rejecting on a transient
    # network problem. httpx raises a RequestError subclass on connect failure.
    validator = UserConfigurationValidator()
    with patch(
        "api.services.configuration.check_validity.httpx.get",
        side_effect=httpx.ConnectError("connection refused"),
    ):
        with pytest.raises(ValueError):
            validator._check_gandr_api_key("tts-1", "gnd-key")


@pytest.mark.asyncio
async def test_gandr_key_validation_does_not_block_event_loop():
    # The bots flagged that a synchronous httpx.get inside the async validate()
    # flow blocks the event loop for up to the request timeout. validate() now
    # runs the sync validator chain via asyncio.to_thread, so a slow Gandr
    # (mocked as a 0.3s blocking call) must NOT stall a concurrent coroutine.
    import time

    validator = UserConfigurationValidator()
    config = EffectiveAIModelConfiguration(
        llm=DograhLLMService(provider="dograh", api_key="dummy", model="tier-1"),
        stt=DograhSTTService(provider="dograh", api_key="dummy", model="stt-1"),
        tts=GandrTTSConfiguration(
            provider="gandr",
            api_key="gnd-valid-key",
            model="tts-1",
            voice="gandr-mia",
        ),
    )

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    def _slow_get(*args, **kwargs):
        time.sleep(0.3)
        return SimpleNamespace(status_code=200)

    # Dograh self-hosted llm/stt keys are validated by a remote client too;
    # stub that so the only network call under test is the slow Gandr httpx.get.
    with (
        patch(
            "api.services.configuration.check_validity.httpx.get", side_effect=_slow_get
        ) as mock_get,
        patch(
            "api.services.configuration.check_validity.mps_service_key_client"
        ) as mock_mps,
    ):
        mock_mps.validate_service_key.return_value = True
        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            await validator.validate(config)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    assert mock_get.call_count == 1
    # If the event loop were blocked by the sync call, the heartbeat (10ms
    # period) would advance ~0 times during the 300ms validation. Running off
    # the loop, it advances many times.
    assert ticks >= 10, f"event loop stalled: only {ticks} heartbeat ticks in 300ms"
