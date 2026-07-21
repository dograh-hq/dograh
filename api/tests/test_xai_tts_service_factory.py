from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pipecat.transcriptions.language import Language

from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    XAI_TTS_VOICES,
    ServiceProviders,
    XAITTSConfiguration,
)
from api.services.pipecat.service_factory import create_tts_service


def test_xai_tts_configuration_defaults():
    config = XAITTSConfiguration(api_key="test-key")

    assert config.provider == ServiceProviders.XAI
    assert config.voice == "eve"
    assert config.language == "en"
    # xAI TTS has no model selector; a constant satisfies the shared contract.
    assert config.model == "xai-tts"
    assert XAI_TTS_VOICES == ["eve", "ara", "leo", "rex", "sal"]


def test_create_xai_tts_service_uses_websocket_streaming():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.XAI.value,
            api_key="test-key",
            model="xai-tts",
            voice="rex",
            language="en",
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )

    with patch("api.services.pipecat.service_factory.XAITTSService") as mock_service:
        create_tts_service(user_config, audio_config)

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    # Sample rate is resolved from the pipeline StartFrame, like other providers.
    assert "sample_rate" not in kwargs
    assert kwargs["settings"].voice == "rex"
    assert kwargs["settings"].language == Language.EN


def test_create_xai_tts_service_converts_language():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.XAI.value,
            api_key="test-key",
            model="xai-tts",
            voice="eve",
            language="fr",
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )

    with patch("api.services.pipecat.service_factory.XAITTSService") as mock_service:
        create_tts_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    assert kwargs["settings"].language == Language.FR


def test_create_xai_tts_service_falls_back_to_english_for_unknown_language():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.XAI.value,
            api_key="test-key",
            model="xai-tts",
            voice="eve",
            language="not-a-language",
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )

    with patch("api.services.pipecat.service_factory.XAITTSService") as mock_service:
        create_tts_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    assert kwargs["settings"].language == Language.EN


def test_create_xai_tts_service_preserves_auto_language():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.XAI.value,
            api_key="test-key",
            model="xai-tts",
            voice="eve",
            language="auto",
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )

    with patch("api.services.pipecat.service_factory.XAITTSService") as mock_service:
        create_tts_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    assert kwargs["settings"].language == "auto"


def test_xai_is_registered_for_key_validation():
    validator = UserConfigurationValidator()
    assert ServiceProviders.XAI.value in validator._validator_map


def test_xai_key_validation_accepts_valid_key():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        assert validator._check_xai_api_key("xai", "xai-valid-key") is True
    # Validates against the TTS-scoped voices endpoint, not /v1/models.
    called_url = mock_get.call_args.args[0]
    assert called_url == "https://api.x.ai/v1/tts/voices"
    assert (
        mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer xai-valid-key"
    )


def test_xai_key_validation_rejects_bad_key():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 401
        with pytest.raises(ValueError):
            validator._check_xai_api_key("xai", "bad-key")


def test_xai_key_validation_allows_scoped_key_without_voice_list_access():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 403
        assert validator._check_xai_api_key("xai", "tts-scoped-key") is True
