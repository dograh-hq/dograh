"""Tests for Azure Speech TTS/STT service factory dispatch."""

from types import SimpleNamespace
from unittest.mock import patch

from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.service_factory import create_stt_service, create_tts_service


def _audio_config():
    return SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )


def test_create_azure_speech_tts_service():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.AZURE_SPEECH.value,
            api_key="test-subscription-key",
            region="eastus",
            voice="en-US-AriaNeural",
            language="en-US",
            speed=1.0,
            model="neural",
        )
    )

    with patch("api.services.pipecat.service_factory.AzureTTSService") as mock_service:
        create_tts_service(user_config, _audio_config())

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["api_key"] == "test-subscription-key"
    assert kwargs["region"] == "eastus"
    assert kwargs["settings"].voice == "en-US-AriaNeural"
    assert kwargs["settings"].language == "en-US"


def test_create_azure_speech_tts_service_with_speed():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.AZURE_SPEECH.value,
            api_key="test-key",
            region="westeurope",
            voice="en-GB-SoniaNeural",
            language="en-GB",
            speed=1.5,
            model="neural",
        )
    )

    with patch("api.services.pipecat.service_factory.AzureTTSService") as mock_service:
        create_tts_service(user_config, _audio_config())

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["region"] == "westeurope"
    assert kwargs["settings"].rate == "1.5"


def test_create_azure_speech_stt_service():
    user_config = SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.AZURE_SPEECH.value,
            api_key="test-subscription-key",
            region="eastus",
            language="en-US",
            model="latest_long",
        )
    )

    with patch("api.services.pipecat.service_factory.AzureSTTService") as mock_service:
        create_stt_service(user_config, _audio_config())

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["api_key"] == "test-subscription-key"
    assert kwargs["region"] == "eastus"
    assert kwargs["sample_rate"] == 16000
