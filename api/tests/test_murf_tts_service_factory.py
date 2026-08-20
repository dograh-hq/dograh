from types import SimpleNamespace
from unittest.mock import patch

import pytest

from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    MURF_TTS_MODELS,
    REGISTRY,
    MurfTTSConfiguration,
    ServiceProviders,
    ServiceType,
)
from api.services.pipecat.murf_tts import MurfTTSService
from api.services.pipecat.service_factory import (
    MURF_DEFAULT_BASE_URL,
    _murf_websocket_url,
    create_tts_service,
)


def test_murf_tts_configuration_defaults():
    config = MurfTTSConfiguration(api_key="test-key")

    assert config.provider == ServiceProviders.MURF
    assert config.voice == "Will"
    assert config.locale == "en-US"
    assert config.model == "falcon-2"
    assert config.base_url == MURF_DEFAULT_BASE_URL
    assert MURF_TTS_MODELS == ["falcon-2"]


def test_murf_is_registered_tts_provider():
    assert REGISTRY[ServiceType.TTS][ServiceProviders.MURF] is MurfTTSConfiguration


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        (
            "https://global.api.murf.ai",
            "wss://global.api.murf.ai/v1/speech/stream-input",
        ),
        (
            "https://in.api.murf.ai",
            "wss://in.api.murf.ai/v1/speech/stream-input",
        ),
        (
            "wss://global.api.murf.ai/v1/speech/stream-input",
            "wss://global.api.murf.ai/v1/speech/stream-input",
        ),
        (None, "wss://global.api.murf.ai/v1/speech/stream-input"),
    ],
)
def test_murf_websocket_url_normalizes_regional_hosts(base_url, expected):
    assert _murf_websocket_url(base_url) == expected


@pytest.mark.parametrize("transport_out_sample_rate", [8000, 16000])
def test_create_murf_tts_service_uses_pipeline_compatible_audio_format(
    transport_out_sample_rate,
):
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.MURF.value,
            api_key="test-key",
            model="falcon-2",
            voice="Will",
            locale="en-IN",
            base_url="https://in.api.murf.ai",
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=transport_out_sample_rate,
        transport_in_sample_rate=16000,
    )

    with patch("api.services.pipecat.service_factory.MurfTTSService") as mock_service:
        # Keep the real nested Pydantic model; patching the class otherwise
        # turns InputParams into a MagicMock and these assertions never see
        # the values the factory actually passed.
        mock_service.InputParams = MurfTTSService.InputParams
        create_tts_service(user_config, audio_config)

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["url"] == "wss://in.api.murf.ai/v1/speech/stream-input"
    assert kwargs["sample_rate"] == transport_out_sample_rate
    assert kwargs["params"].voice_id == "Will"
    assert kwargs["params"].model == "falcon-2"
    assert kwargs["params"].multi_native_locale == "en-IN"
    assert kwargs["params"].sample_rate == transport_out_sample_rate
    assert kwargs["params"].format == "PCM"
    assert kwargs["params"].channel_type == "MONO"
    assert kwargs["params"].min_buffer_size == 2
    assert kwargs["params"].max_buffer_delay_in_ms == 0


def test_create_murf_tts_service_defaults_locale_and_host():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.MURF.value,
            api_key="test-key",
            model=None,
            voice=None,
            locale=None,
            base_url=None,
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=16000,
        transport_in_sample_rate=16000,
    )

    with patch("api.services.pipecat.service_factory.MurfTTSService") as mock_service:
        mock_service.InputParams = MurfTTSService.InputParams
        create_tts_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    assert kwargs["url"] == "wss://global.api.murf.ai/v1/speech/stream-input"
    assert kwargs["params"].voice_id == "Will"
    assert kwargs["params"].model == "falcon-2"
    assert kwargs["params"].multi_native_locale == "en-US"


def test_murf_is_registered_for_key_validation():
    validator = UserConfigurationValidator()
    assert ServiceProviders.MURF.value in validator._validator_map


def test_murf_key_validation_accepts_valid_key():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        assert validator._check_murf_api_key("falcon-2", "murf-valid-key") is True
    called_url = mock_get.call_args.args[0]
    assert called_url == "https://api.murf.ai/v1/speech/voices"
    assert mock_get.call_args.kwargs["headers"]["api-key"] == "murf-valid-key"


def test_murf_key_validation_rejects_bad_key():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 401
        with pytest.raises(ValueError):
            validator._check_murf_api_key("falcon-2", "bad-key")
