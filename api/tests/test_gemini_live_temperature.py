from types import SimpleNamespace

import pytest
from google.auth.credentials import AnonymousCredentials

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


@pytest.mark.parametrize(
    "config_cls, required_fields",
    [
        (GoogleRealtimeLLMConfiguration, {"api_key": "test-key"}),
        (GoogleVertexRealtimeLLMConfiguration, {"project_id": "test-proj"}),
    ],
)
def test_google_realtime_config_does_not_expose_temperature(
    config_cls, required_fields
):
    assert "temperature" not in config_cls.model_json_schema()["properties"]
    # Previously saved configurations still load, but discard temperature.
    config = config_cls.model_validate({**required_fields, "temperature": 0.0})
    assert "temperature" not in config.model_dump()
    assert not hasattr(config, "temperature")


def test_openai_realtime_config_does_not_expose_temperature():
    # The OpenAI Realtime GA interface does not accept temperature.
    assert "temperature" not in OpenAIRealtimeLLMConfiguration.model_fields


@pytest.mark.parametrize(
    "model", ["gemini-3.1-flash-live-preview", "custom-gemini-live-model"]
)
def test_create_realtime_llm_service_gemini_live_uses_provider_temperature(model):
    realtime_config = GoogleRealtimeLLMConfiguration(
        api_key="test-api-key",
        model=model,
        voice="Puck",
    )
    # Workflow overrides use model_copy, which can retain removed fields.
    realtime_config = realtime_config.model_copy(update={"temperature": 0.0})
    user_config = DummyUserConfig(realtime_config)

    service = create_realtime_llm_service(user_config, _audio_config())
    assert isinstance(service, DograhGeminiLiveLLMService)
    assert service._settings.temperature is None


def test_create_realtime_llm_service_gemini_vertex_uses_provider_temperature(
    monkeypatch,
):
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
    )
    realtime_config = realtime_config.model_copy(update={"temperature": 1.2})
    user_config = DummyUserConfig(realtime_config)

    service = create_realtime_llm_service(user_config, _audio_config())
    assert isinstance(service, DograhGeminiLiveVertexLLMService)
    assert service._settings.temperature is None
