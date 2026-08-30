from types import SimpleNamespace

import pytest
from google.auth.credentials import AnonymousCredentials
from pydantic import ValidationError

from api.services.configuration.registry import (
    GoogleRealtimeLLMConfiguration,
    GoogleVertexRealtimeLLMConfiguration,
    OpenAIRealtimeLLMConfiguration,
)
from api.services.pipecat.realtime.gemini_live import DograhGeminiLiveLLMService
from api.services.pipecat.realtime.gemini_live_vertex import (
    DograhGeminiLiveVertexLLMService,
)
from api.services.pipecat.service_factory import create_realtime_llm_service


class DummyUserConfig:
    def __init__(self, realtime_config):
        self.realtime = realtime_config


def _audio_config():
    return SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )


def test_google_realtime_config_temperature_validation():
    # Valid temperatures
    cfg = GoogleRealtimeLLMConfiguration(
        api_key="test-key",
        model="gemini-3.1-flash-live-preview",
        temperature=0.7,
    )
    assert cfg.temperature == 0.7

    cfg_vertex = GoogleVertexRealtimeLLMConfiguration(
        project_id="test-proj",
        model="google/gemini-live-2.5-flash-native-audio",
        temperature=0.2,
    )
    assert cfg_vertex.temperature == 0.2

    # Out of range temperature (< 0.0)
    with pytest.raises(ValidationError):
        GoogleRealtimeLLMConfiguration(
            api_key="test-key",
            temperature=-0.1,
        )

    # Out of range temperature (> 2.0)
    with pytest.raises(ValidationError):
        GoogleRealtimeLLMConfiguration(
            api_key="test-key",
            temperature=2.5,
        )


def test_openai_realtime_config_does_not_expose_temperature():
    # The OpenAI Realtime GA interface does not accept temperature.
    assert "temperature" not in OpenAIRealtimeLLMConfiguration.model_fields


def test_create_realtime_llm_service_gemini_live_temperature():
    realtime_config = GoogleRealtimeLLMConfiguration(
        api_key="test-api-key",
        model="gemini-3.1-flash-live-preview",
        voice="Puck",
        temperature=0.35,
    )
    user_config = DummyUserConfig(realtime_config)

    service = create_realtime_llm_service(user_config, _audio_config())
    assert isinstance(service, DograhGeminiLiveLLMService)
    assert service._settings.temperature == 0.35


def test_create_realtime_llm_service_gemini_vertex_temperature(monkeypatch):
    # Keep this factory test hermetic: the Vertex service normally refreshes
    # Application Default Credentials during construction.
    monkeypatch.setattr(
        DograhGeminiLiveVertexLLMService,
        "_get_credentials",
        staticmethod(lambda _credentials, _credentials_path: AnonymousCredentials()),
    )

    realtime_config = GoogleVertexRealtimeLLMConfiguration(
        project_id="test-proj",
        location="us-central1",
        model="google/gemini-live-2.5-flash-native-audio",
        voice="Charon",
        temperature=1.2,
    )
    user_config = DummyUserConfig(realtime_config)

    service = create_realtime_llm_service(user_config, _audio_config())
    assert isinstance(service, DograhGeminiLiveVertexLLMService)
    assert service._settings.temperature == 1.2
