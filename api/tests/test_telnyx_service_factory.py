from types import SimpleNamespace
from unittest.mock import patch

import pytest

from api.services.configuration.registry import (
    ServiceProviders,
    TelnyxTTSConfiguration,
    TelnyxSTTConfiguration,
    TELNYX_TTS_VOICES,
    TELNYX_STT_MODELS,
    TELNYX_STT_INPUT_FORMATS,
)
from api.services.pipecat.service_factory import create_tts_service, create_stt_service


def test_telnyx_tts_configuration_defaults():
    config = TelnyxTTSConfiguration(api_key="test-key")

    assert config.provider == ServiceProviders.TELNYX
    assert config.voice == "Telnyx.NaturalHD.astra"
    assert config.model == "natural-hd"
    assert config.language == "en"
    assert config.speed == 1.0
    assert "Telnyx.NaturalHD.astra" in TELNYX_TTS_VOICES


def test_telnyx_stt_configuration_defaults():
    config = TelnyxSTTConfiguration(api_key="test-key")

    assert config.provider == ServiceProviders.TELNYX
    assert config.model == "Telnyx"
    assert config.language == "en"
    assert config.input_format == "linear16"
    assert "Telnyx" in TELNYX_STT_MODELS
    assert "linear16" in TELNYX_STT_INPUT_FORMATS


@pytest.mark.parametrize("transport_out_sample_rate", [8000, 16000, 24000])
def test_create_telnyx_tts_service_passes_config(transport_out_sample_rate):
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.TELNYX.value,
            api_key="test-key",
            voice="Telnyx.NaturalHD.luna",
            model="natural-hd",
            language="es",
            speed=1.2,
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=transport_out_sample_rate,
        transport_in_sample_rate=16000,
    )

    with patch("api.services.pipecat.telnyx_tts.TelnyxTTSService") as mock_service:
        create_tts_service(user_config, audio_config)

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["voice"] == "Telnyx.NaturalHD.luna"
    assert kwargs["model"] == "natural-hd"
    assert kwargs["language"] == "es"
    assert kwargs["speed"] == 1.2
    assert kwargs["sample_rate"] == transport_out_sample_rate


@pytest.mark.parametrize("transport_in_sample_rate", [8000, 16000])
def test_create_telnyx_stt_service_passes_config(transport_in_sample_rate):
    user_config = SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.TELNYX.value,
            api_key="test-key",
            model="Deepgram",
            language="en",
            input_format="linear16",
        )
    )
    audio_config = SimpleNamespace(
        transport_in_sample_rate=transport_in_sample_rate,
        transport_out_sample_rate=16000,
    )

    with patch("api.services.pipecat.telnyx_stt.TelnyxSTTService") as mock_service:
        create_stt_service(user_config, audio_config)

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["transcription_engine"] == "Deepgram"
    assert kwargs["input_format"] == "linear16"
    assert kwargs["sample_rate"] == transport_in_sample_rate
    assert kwargs["language"] == "en"
