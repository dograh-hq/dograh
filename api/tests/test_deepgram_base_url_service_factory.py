from types import SimpleNamespace
from unittest.mock import patch

from api.services.configuration.registry import (
    DeepgramSTTConfiguration,
    DeepgramTTSConfiguration,
    ServiceProviders,
)
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.service_factory import create_stt_service, create_tts_service

ON_PREM_URL = "wss://deepgram.internal.example.com"


def _audio_config() -> AudioConfig:
    return AudioConfig(
        transport_in_sample_rate=16000,
        transport_out_sample_rate=16000,
    )


def _stt_config(base_url: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.DEEPGRAM.value,
            api_key="test-key",
            model="nova-3-general",
            language="multi",
            base_url=base_url,
        )
    )


def _tts_config(base_url: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.DEEPGRAM.value,
            api_key="test-key",
            model="aura-2",
            voice="aura-2-helena-en",
            base_url=base_url,
        )
    )


def test_deepgram_configurations_default_to_empty_base_url():
    assert DeepgramSTTConfiguration(api_key="test-key").base_url == ""
    assert DeepgramTTSConfiguration(api_key="test-key").base_url == ""


def test_deepgram_stt_passes_custom_base_url():
    with patch("api.services.pipecat.service_factory.DeepgramSTTService") as stt_service:
        create_stt_service(_stt_config(ON_PREM_URL), _audio_config())

    kwargs = stt_service.call_args.kwargs
    assert kwargs["base_url"] == ON_PREM_URL
    assert kwargs["api_key"] == "test-key"


def test_deepgram_stt_omits_base_url_when_unset():
    with patch("api.services.pipecat.service_factory.DeepgramSTTService") as stt_service:
        create_stt_service(_stt_config(), _audio_config())

    assert "base_url" not in stt_service.call_args.kwargs


def test_deepgram_tts_passes_custom_base_url():
    with patch("api.services.pipecat.service_factory.DeepgramTTSService") as tts_service:
        create_tts_service(_tts_config(ON_PREM_URL), _audio_config())

    kwargs = tts_service.call_args.kwargs
    assert kwargs["base_url"] == ON_PREM_URL
    assert kwargs["settings"].voice == "aura-2-helena-en"


def test_deepgram_tts_omits_base_url_when_unset():
    with patch("api.services.pipecat.service_factory.DeepgramTTSService") as tts_service:
        create_tts_service(_tts_config(), _audio_config())

    assert "base_url" not in tts_service.call_args.kwargs
