from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pipecat.transcriptions.language import Language

from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    SPEECHIFY_TTS_MODELS,
    SPEECHIFY_TTS_VOICES,
    ServiceProviders,
    SpeechifyTTSConfiguration,
)
from api.services.pipecat.service_factory import create_tts_service


def test_speechify_tts_configuration_defaults():
    config = SpeechifyTTSConfiguration(api_key="test-key")

    assert config.provider == ServiceProviders.SPEECHIFY
    assert config.model == "simba-3.2"
    assert config.voice == "beatrice_32"
    assert config.language == "en"
    assert SPEECHIFY_TTS_MODELS[0] == "simba-3.2"
    assert "beatrice_32" in SPEECHIFY_TTS_VOICES


@pytest.mark.parametrize("transport_out_sample_rate", [8000, 16000, 24000])
def test_create_speechify_tts_service_uses_transport_sample_rate(
    transport_out_sample_rate,
):
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.SPEECHIFY.value,
            api_key="test-key",
            model="simba-3.2",
            voice="geffen_32",
            language="en",
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=transport_out_sample_rate,
        transport_in_sample_rate=16000,
    )

    with patch(
        "api.services.pipecat.service_factory.SpeechifyTTSService"
    ) as mock_service:
        create_tts_service(user_config, audio_config)

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["sample_rate"] == transport_out_sample_rate
    assert kwargs["settings"].voice == "geffen_32"
    assert kwargs["settings"].model == "simba-3.2"
    assert kwargs["settings"].language == Language.EN


def test_create_speechify_tts_service_converts_language():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.SPEECHIFY.value,
            api_key="test-key",
            model="simba-3.0",
            voice="adriana",
            language="pt-BR",
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )

    with patch(
        "api.services.pipecat.service_factory.SpeechifyTTSService"
    ) as mock_service:
        create_tts_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    assert kwargs["settings"].language == Language.PT_BR


def test_create_speechify_tts_service_falls_back_to_english_for_unknown_language():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.SPEECHIFY.value,
            api_key="test-key",
            model="simba-3.2",
            voice="beatrice_32",
            language="not-a-language",
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )

    with patch(
        "api.services.pipecat.service_factory.SpeechifyTTSService"
    ) as mock_service:
        create_tts_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    assert kwargs["settings"].language == Language.EN


def test_speechify_is_registered_for_key_validation():
    validator = UserConfigurationValidator()
    assert ServiceProviders.SPEECHIFY.value in validator._validator_map


def test_speechify_key_validation_accepts_valid_key():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        assert validator._check_speechify_api_key("simba-3.2", "sk-valid-key") is True
    called_url = mock_get.call_args.args[0]
    assert called_url == "https://api.speechify.ai/v1/voices?limit=1"
    headers = mock_get.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer sk-valid-key"


def test_speechify_key_validation_treats_non_auth_errors_as_inconclusive():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 500
        assert validator._check_speechify_api_key("simba-3.2", "sk-valid-key") is True


def test_speechify_key_validation_rejects_bad_key():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 401
        with pytest.raises(ValueError):
            validator._check_speechify_api_key("simba-3.2", "bad-key")
