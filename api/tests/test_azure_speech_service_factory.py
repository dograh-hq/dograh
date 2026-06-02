"""Tests for Azure Speech TTS/STT service factory dispatch."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    AzureRealtimeLLMConfiguration,
    AzureSpeechSTTConfiguration,
    AzureSpeechTTSConfiguration,
    ServiceProviders,
)
from api.services.gen_ai.embedding.azure_openai_service import (
    AzureOpenAIEmbeddingService,
)
from api.services.pipecat.service_factory import (
    create_realtime_llm_service,
    create_stt_service,
    create_tts_service,
)


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


def test_create_azure_speech_stt_service_preserves_custom_language():
    user_config = SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.AZURE_SPEECH.value,
            api_key="test-subscription-key",
            region="eastus",
            language="custom-locale",
            model="latest_long",
        )
    )

    with patch("api.services.pipecat.service_factory.AzureSTTService") as mock_service:
        create_stt_service(user_config, _audio_config())

    kwargs = mock_service.call_args.kwargs
    assert kwargs["settings"].language == "custom-locale"


def test_validator_accepts_azure_speech_services():
    validator = UserConfigurationValidator()

    assert (
        validator._validate_service(
            AzureSpeechTTSConfiguration(api_key="test-key"),
            "tts",
        )
        == []
    )
    assert (
        validator._validate_service(
            AzureSpeechSTTConfiguration(api_key="test-key"),
            "stt",
        )
        == []
    )


def test_validator_accepts_azure_realtime_service(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "oss")
    validator = UserConfigurationValidator()

    assert (
        validator._validate_service(
            AzureRealtimeLLMConfiguration(
                api_key="test-key",
                endpoint="https://example.openai.azure.com",
            ),
            "realtime",
        )
        == []
    )


def test_create_azure_realtime_blocks_private_endpoint_in_saas(monkeypatch):
    monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")
    user_config = SimpleNamespace(
        realtime=SimpleNamespace(
            provider=ServiceProviders.AZURE_REALTIME.value,
            api_key="test-key",
            endpoint="http://10.0.0.10",
            api_version="2025-04-01-preview",
            model="gpt-4o-realtime-preview",
            voice="alloy",
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        create_realtime_llm_service(user_config, _audio_config())

    assert exc_info.value.status_code == 400
    assert "public IP" in exc_info.value.detail


def test_azure_embedding_service_rejects_wrong_dimension():
    service = AzureOpenAIEmbeddingService(
        db_client=SimpleNamespace(),
        api_key=None,
        endpoint=None,
        model_id="text-embedding-3-large",
    )

    with pytest.raises(ValueError, match="1536-dimensional"):
        service._validate_embedding_dimensions([[0.0] * 3072])
