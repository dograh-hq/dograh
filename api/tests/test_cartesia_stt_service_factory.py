from types import SimpleNamespace
from unittest.mock import patch

from api.services.configuration.options import (
    CARTESIA_INK_2_STT_LANGUAGES,
    CARTESIA_INK_WHISPER_STT_LANGUAGES,
    CARTESIA_STT_MODELS,
)
from api.services.configuration.registry import (
    CartesiaSTTConfiguration,
    ServiceProviders,
)
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.service_factory import (
    create_stt_service,
    stt_uses_external_turns,
)


def _audio_config() -> AudioConfig:
    return AudioConfig(
        transport_in_sample_rate=16000,
        transport_out_sample_rate=16000,
    )


def _cartesia_config(model: str, language: str = "en") -> SimpleNamespace:
    return SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.CARTESIA.value,
            api_key="test-key",
            model=model,
            language=language,
        )
    )


def test_cartesia_stt_configuration_exposes_ink_2_and_ink_whisper_languages():
    config = CartesiaSTTConfiguration(api_key="test-key")
    language_schema = CartesiaSTTConfiguration.model_json_schema()["properties"][
        "language"
    ]

    assert config.provider == ServiceProviders.CARTESIA
    assert config.model == "ink-whisper"
    assert config.language == "en"
    assert CARTESIA_STT_MODELS == ["ink-2", "ink-whisper"]
    assert CARTESIA_INK_2_STT_LANGUAGES == ("en",)
    assert "es" in CARTESIA_INK_WHISPER_STT_LANGUAGES
    assert language_schema["model_options"]["ink-2"] == ["en"]
    assert "es" in language_schema["model_options"]["ink-whisper"]


def test_cartesia_ink_2_uses_external_turns_and_turns_service():
    user_config = _cartesia_config("ink-2")

    assert stt_uses_external_turns(user_config)

    with (
        patch(
            "api.services.pipecat.service_factory.CartesiaTurnsSTTService"
        ) as turns_service,
        patch("api.services.pipecat.service_factory.CartesiaSTTService") as stt_service,
    ):
        create_stt_service(user_config, _audio_config())

    turns_service.assert_called_once()
    stt_service.assert_not_called()
    kwargs = turns_service.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["sample_rate"] == 16000
    assert kwargs["should_interrupt"] is False


def test_cartesia_ink_whisper_uses_manual_stt_service_with_model_and_language():
    user_config = _cartesia_config("ink-whisper", language="es")

    assert not stt_uses_external_turns(user_config)

    with (
        patch(
            "api.services.pipecat.service_factory.CartesiaTurnsSTTService"
        ) as turns_service,
        patch("api.services.pipecat.service_factory.CartesiaSTTService") as stt_service,
    ):
        create_stt_service(user_config, _audio_config())

    turns_service.assert_not_called()
    stt_service.assert_called_once()
    kwargs = stt_service.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["sample_rate"] == 16000
    assert kwargs["settings"].model == "ink-whisper"
    assert kwargs["settings"].language == "es"
