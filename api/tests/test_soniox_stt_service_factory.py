from types import SimpleNamespace
from unittest.mock import patch

from api.services.configuration.options import (
    SONIOX_STT_LANGUAGES,
    SONIOX_STT_MODELS,
)
from api.services.configuration.registry import (
    ServiceProviders,
    SonioxSTTConfiguration,
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


def _soniox_config(language: str = "auto", model: str = "stt-rt-v5") -> SimpleNamespace:
    return SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.SONIOX.value,
            api_key="test-key",
            model=model,
            language=language,
        )
    )


def test_soniox_stt_configuration_defaults_and_options():
    config = SonioxSTTConfiguration(api_key="test-key")

    assert config.provider == ServiceProviders.SONIOX
    assert config.model == "stt-rt-v5"
    assert config.language == "auto"
    assert SONIOX_STT_MODELS[0] == "stt-rt-v5"
    assert "auto" in SONIOX_STT_LANGUAGES
    assert "bn" in SONIOX_STT_LANGUAGES


def test_soniox_uses_external_turns():
    assert stt_uses_external_turns(_soniox_config())


def test_soniox_auto_language_enables_language_identification():
    user_config = _soniox_config(language="auto")

    with patch(
        "api.services.pipecat.service_factory.SonioxSTTService"
    ) as soniox_service:
        create_stt_service(user_config, _audio_config())

    soniox_service.assert_called_once()
    kwargs = soniox_service.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["sample_rate"] == 16000
    # Soniox drives its own end-of-turn detection.
    assert kwargs["vad_force_turn_endpoint"] is False
    assert kwargs["should_interrupt"] is False
    settings = kwargs["settings"]
    assert settings.model == "stt-rt-v5"
    assert settings.language_hints is None
    assert settings.enable_language_identification is True


def test_soniox_language_hint_disables_auto_identification():
    user_config = _soniox_config(language="bn")

    with patch(
        "api.services.pipecat.service_factory.SonioxSTTService"
    ) as soniox_service:
        create_stt_service(user_config, _audio_config())

    kwargs = soniox_service.call_args.kwargs
    settings = kwargs["settings"]
    assert settings.language_hints is not None
    assert len(settings.language_hints) == 1
    assert settings.enable_language_identification is False
