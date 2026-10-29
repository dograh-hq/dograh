from types import SimpleNamespace
from unittest.mock import patch

from pipecat.services.settings import NOT_GIVEN
from pipecat.transcriptions.language import Language
from pydantic import TypeAdapter

from api.services.configuration.options import SONIOX_STT_LANGUAGES, SONIOX_STT_MODELS
from api.services.configuration.registry import (
    ServiceProviders,
    SonioxSTTConfiguration,
    STTConfig,
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


def _soniox_config(**stt) -> SimpleNamespace:
    config = TypeAdapter(STTConfig).validate_python(
        {"provider": "soniox", "api_key": "test-key", **stt}
    )
    return SimpleNamespace(stt=config)


def test_soniox_stt_configuration_exposes_defaults_and_languages():
    config = SonioxSTTConfiguration(api_key="test-key")
    language_schema = SonioxSTTConfiguration.model_json_schema()["properties"][
        "language"
    ]

    assert config.provider == ServiceProviders.SONIOX
    assert config.model == "stt-rt-v5"
    assert config.language == "multi"
    assert SONIOX_STT_MODELS == ("stt-rt-v5",)
    assert "multi" in SONIOX_STT_LANGUAGES
    assert "hi" in SONIOX_STT_LANGUAGES
    assert language_schema["examples"] == list(SONIOX_STT_LANGUAGES)


def test_soniox_stt_config_parses_from_stt_union():
    user_config = _soniox_config(language="hi")

    assert isinstance(user_config.stt, SonioxSTTConfiguration)
    assert user_config.stt.language == "hi"


def test_soniox_stt_uses_local_turns_with_language_hint():
    user_config = _soniox_config(language="hi")

    assert not stt_uses_external_turns(user_config)

    with patch("api.services.pipecat.service_factory.SonioxSTTService") as stt_service:
        create_stt_service(user_config, _audio_config())

    stt_service.assert_called_once()
    kwargs = stt_service.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["sample_rate"] == 16000
    assert kwargs["vad_force_turn_endpoint"] is True
    assert kwargs["settings"].model == "stt-rt-v5"
    assert kwargs["settings"].language_hints == [Language.HI]


def test_soniox_stt_multi_language_sends_no_hints():
    user_config = _soniox_config()

    with patch("api.services.pipecat.service_factory.SonioxSTTService") as stt_service:
        create_stt_service(user_config, _audio_config())

    kwargs = stt_service.call_args.kwargs
    assert kwargs["settings"].language_hints is NOT_GIVEN
    assert kwargs["settings"].context is NOT_GIVEN


def test_soniox_stt_passes_keyterms_as_context_terms():
    user_config = _soniox_config(language="en")

    with patch("api.services.pipecat.service_factory.SonioxSTTService") as stt_service:
        create_stt_service(user_config, _audio_config(), keyterms=["Dograh", "Soniox"])

    kwargs = stt_service.call_args.kwargs
    assert kwargs["settings"].context.terms == ["Dograh", "Soniox"]
