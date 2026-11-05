from types import SimpleNamespace

import pytest
from google.auth.credentials import AnonymousCredentials
from google.genai.types import AudioTranscriptionConfig, LiveConnectConfig
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService

from api.services.configuration.registry import GoogleRealtimeLLMConfiguration
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


def _service(**settings_kwargs) -> DograhGeminiLiveLLMService:
    return DograhGeminiLiveLLMService(
        api_key="test-api-key",
        settings=DograhGeminiLiveLLMService.Settings(
            model="gemini-3.1-flash-live-preview", voice="Puck", **settings_kwargs
        ),
    )


def _connect_config(**input_kwargs) -> LiveConnectConfig:
    # Mirrors upstream _connect(): transcription enabled with an empty config.
    return LiveConnectConfig(
        input_audio_transcription=AudioTranscriptionConfig(**input_kwargs),
        output_audio_transcription=AudioTranscriptionConfig(),
    )


def test_configured_language_becomes_input_transcription_hint():
    service = _service(language="ka-GE")
    config = _connect_config()

    service._apply_input_transcription_language(config)

    assert config.input_audio_transcription.language_codes == ["ka-GE"]
    # Only the caller's audio gets the hint; the model's own speech is untouched.
    assert not config.output_audio_transcription.language_codes


def test_no_hint_without_configured_language():
    # Upstream fills in an en-US default; that must not become a hint that
    # replaces auto-detection.
    service = _service()
    config = _connect_config()

    service._apply_input_transcription_language(config)

    assert not config.input_audio_transcription.language_codes


def test_existing_language_codes_are_kept():
    service = _service(language="ka-GE")
    config = _connect_config(language_codes=["en-US", "ka-GE"])

    service._apply_input_transcription_language(config)

    assert config.input_audio_transcription.language_codes == ["en-US", "ka-GE"]


def test_disabled_input_transcription_stays_disabled():
    service = _service(language="ka-GE")
    config = LiveConnectConfig(input_audio_transcription=None)

    service._apply_input_transcription_language(config)

    assert config.input_audio_transcription is None


def test_vertex_service_does_not_send_hint(monkeypatch):
    monkeypatch.setattr(
        DograhGeminiLiveVertexLLMService,
        "_get_credentials",
        staticmethod(lambda _credentials, _credentials_path: AnonymousCredentials()),
    )
    service = DograhGeminiLiveVertexLLMService(
        credentials=None,
        project_id="test-proj",
        location="us-central1",
        settings=DograhGeminiLiveVertexLLMService.Settings(
            model="google/gemini-live-2.5-flash-native-audio",
            voice="Charon",
            language="ka-GE",
        ),
    )
    config = _connect_config()

    service._apply_input_transcription_language(config)

    assert not config.input_audio_transcription.language_codes


async def test_connection_handler_applies_hint_before_connecting(monkeypatch):
    seen: list[LiveConnectConfig] = []

    async def fake_parent_handler(self, config):
        seen.append(config)

    monkeypatch.setattr(
        GeminiLiveLLMService, "_connection_task_handler", fake_parent_handler
    )
    service = _service(language="ka-GE")

    await service._connection_task_handler(_connect_config())

    assert seen[0].input_audio_transcription.language_codes == ["ka-GE"]


@pytest.mark.parametrize("language", ["ka-GE", "en"])
def test_factory_passes_workflow_language_as_hint(language):
    realtime_config = GoogleRealtimeLLMConfiguration(
        api_key="test-api-key",
        model="gemini-3.1-flash-live-preview",
        voice="Puck",
        language=language,
    )

    service = create_realtime_llm_service(
        DummyUserConfig(realtime_config), _audio_config()
    )
    config = _connect_config()
    service._apply_input_transcription_language(config)

    assert config.input_audio_transcription.language_codes == [language]
