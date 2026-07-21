from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pipecat.transcriptions.language import Language

from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    LMNT_TTS_MODELS,
    LMNT_TTS_VOICES,
    LmntTTSConfiguration,
    ServiceProviders,
)
from api.services.pipecat.service_factory import create_tts_service


def test_lmnt_tts_configuration_defaults():
    config = LmntTTSConfiguration(api_key="test-key")

    assert config.provider == ServiceProviders.LMNT
    assert config.voice == "lily"
    assert config.language == "en"
    assert config.model == "aurora"
    assert LMNT_TTS_MODELS == ["aurora", "blizzard"]
    assert "lily" in LMNT_TTS_VOICES


@pytest.mark.parametrize("transport_out_sample_rate", [8000, 16000])
def test_create_lmnt_tts_service_uses_pipeline_compatible_audio_format(
    transport_out_sample_rate,
):
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.LMNT.value,
            api_key="test-key",
            model="blizzard",
            voice="daniel",
            language="en",
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=transport_out_sample_rate,
        transport_in_sample_rate=16000,
    )

    with patch("api.services.pipecat.service_factory.LmntTTSService") as mock_service:
        create_tts_service(user_config, audio_config)

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["sample_rate"] == transport_out_sample_rate
    # Raw PCM keeps the audio compatible with the telephony/output transport.
    assert kwargs["output_format"] == "pcm_s16le"
    assert kwargs["settings"].voice == "daniel"
    assert kwargs["settings"].model == "blizzard"
    assert kwargs["settings"].language == Language.EN


def test_create_lmnt_tts_service_converts_language():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.LMNT.value,
            api_key="test-key",
            model="aurora",
            voice="lily",
            language="fr",
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )

    with patch("api.services.pipecat.service_factory.LmntTTSService") as mock_service:
        create_tts_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    assert kwargs["settings"].language == Language.FR


def test_create_lmnt_tts_service_falls_back_to_english_for_unknown_language():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.LMNT.value,
            api_key="test-key",
            model="aurora",
            voice="lily",
            language="not-a-language",
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )

    with patch("api.services.pipecat.service_factory.LmntTTSService") as mock_service:
        create_tts_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    assert kwargs["settings"].language == Language.EN


def test_lmnt_is_registered_for_key_validation():
    validator = UserConfigurationValidator()
    assert ServiceProviders.LMNT.value in validator._validator_map


def test_lmnt_key_validation_accepts_valid_key():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        assert validator._check_lmnt_api_key("aurora", "lmnt-valid-key") is True
    called_url = mock_get.call_args.args[0]
    assert called_url == "https://api.lmnt.com/v1/ai/voice/list"
    headers = mock_get.call_args.kwargs["headers"]
    assert headers["X-API-Key"] == "lmnt-valid-key"
    # LMNT requires the version header; without it the request is rejected as
    # malformed rather than authenticated, defeating the smoke test.
    assert headers["lmnt-version"] == "1.1"


def test_lmnt_key_validation_rejects_bad_key():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 401
        with pytest.raises(ValueError):
            validator._check_lmnt_api_key("aurora", "bad-key")
