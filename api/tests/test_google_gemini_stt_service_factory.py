from types import SimpleNamespace
from unittest.mock import patch

from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.gemini_stt import DograhGeminiSTTService
from api.services.pipecat.service_factory import create_stt_service
from pipecat.services.google.gemini_live.stt import GeminiSTTSettings


def _user_config(*, language: str = "multi", mode: str = "VERBATIM"):
    return SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.GOOGLE_GEMINI.value,
            api_key="gemini-api-key",
            model="gemini-3.5-transcribe-live",
            language=language,
            mode=mode,
        )
    )


def test_create_google_gemini_stt_service_uses_auto_language_and_keyterms():
    audio_config = SimpleNamespace(transport_in_sample_rate=16000)

    with patch(
        "api.services.pipecat.service_factory.DograhGeminiSTTService"
    ) as mock_service:
        create_stt_service(
            _user_config(mode="SMART"),
            audio_config,
            keyterms=["Dograh", "Pipecat"],
        )

    kwargs = mock_service.call_args.kwargs
    assert kwargs["api_key"] == "gemini-api-key"
    assert kwargs["sample_rate"] == 16000
    assert kwargs["should_interrupt"] is False
    assert kwargs["transcription_mode"] == "SMART"
    assert kwargs["custom_vocabulary"] == ["Dograh", "Pipecat"]
    assert kwargs["settings"].model == "gemini-3.5-transcribe-live"
    assert kwargs["settings"].language is None


def test_create_google_gemini_stt_service_uses_language_hint():
    audio_config = SimpleNamespace(transport_in_sample_rate=8000)

    with patch(
        "api.services.pipecat.service_factory.DograhGeminiSTTService"
    ) as mock_service:
        create_stt_service(_user_config(language="en-IN"), audio_config)

    kwargs = mock_service.call_args.kwargs
    assert kwargs["sample_rate"] == 8000
    assert kwargs["settings"].language == "en-IN"


def test_google_gemini_stt_builds_public_live_transcription_config():
    with patch.object(DograhGeminiSTTService, "_create_client"):
        service = DograhGeminiSTTService(
            api_key="gemini-api-key",
            settings=GeminiSTTSettings(
                model="gemini-3.5-transcribe-live",
                language="en-US",
            ),
            transcription_mode="SMART",
            custom_vocabulary=["Dograh", "Pipecat"],
            sample_rate=16000,
        )

    config = service._build_live_config()
    transcription = config.input_audio_transcription

    assert transcription is not None
    assert transcription.language_codes == ["en-US"]
    assert transcription.custom_vocabulary == ["Dograh", "Pipecat"]
    assert transcription.mode.value == "SMART"
