import importlib.util
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from pipecat.transcriptions.language import Language

from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    SPEECHIFY_TTS_LANGUAGES_BY_MODEL,
    SPEECHIFY_TTS_MODELS,
    SPEECHIFY_TTS_VOICES,
    SPEECHIFY_TTS_VOICES_BY_MODEL,
    ServiceProviders,
    SpeechifyTTSConfiguration,
)
from api.services.pipecat.service_factory import create_tts_service


@pytest.fixture
def speechify_pipecat_stub(monkeypatch):
    """Provide pipecat.services.speechify.tts for the factory's lazy import.

    Stubs the module only when the pinned pipecat checkout predates upstream's
    SpeechifyHttpTTSService. Once the submodule includes the real module, the
    tests run against the real classes so signature drift fails loudly.
    """
    try:
        real_module_present = (
            importlib.util.find_spec("pipecat.services.speechify.tts") is not None
        )
    except ModuleNotFoundError:
        real_module_present = False
    if real_module_present:
        sys.modules.pop("api.services.pipecat.speechify_tts", None)
        yield
        sys.modules.pop("api.services.pipecat.speechify_tts", None)
        return

    class StubSpeechifyHttpTTSService:
        def __init__(self, *args, **kwargs):
            pass

        async def cleanup(self):
            pass

    class StubSpeechifyTTSSettings:
        def __init__(self, voice=None, model=None, language=None):
            self.voice = voice
            self.model = model
            self.language = language

    tts_mod = types.ModuleType("pipecat.services.speechify.tts")
    tts_mod.SpeechifyHttpTTSService = StubSpeechifyHttpTTSService
    tts_mod.SpeechifyTTSSettings = StubSpeechifyTTSSettings
    pkg = types.ModuleType("pipecat.services.speechify")
    pkg.tts = tts_mod
    monkeypatch.setitem(sys.modules, "pipecat.services.speechify", pkg)
    monkeypatch.setitem(sys.modules, "pipecat.services.speechify.tts", tts_mod)
    # Force the wrapper module to rebind against this stub.
    sys.modules.pop("api.services.pipecat.speechify_tts", None)
    yield
    sys.modules.pop("api.services.pipecat.speechify_tts", None)


def test_speechify_tts_configuration_defaults():
    config = SpeechifyTTSConfiguration(api_key="test-key")

    assert config.provider == ServiceProviders.SPEECHIFY
    assert config.model == "simba-3.2"
    assert config.voice == "beatrice_32"
    assert config.language == "en"
    assert SPEECHIFY_TTS_MODELS[0] == "simba-3.2"
    assert "beatrice_32" in SPEECHIFY_TTS_VOICES


def test_speechify_language_options_cover_every_model():
    # The language dropdown is filtered per model via model_options; every
    # selectable model needs an entry, and simba-3.2 is documented English-only.
    assert set(SPEECHIFY_TTS_LANGUAGES_BY_MODEL) == set(SPEECHIFY_TTS_MODELS)
    assert SPEECHIFY_TTS_LANGUAGES_BY_MODEL["simba-3.2"] == ["en"]
    language_extra = SpeechifyTTSConfiguration.model_fields[
        "language"
    ].json_schema_extra
    assert language_extra["model_options"] is SPEECHIFY_TTS_LANGUAGES_BY_MODEL


@pytest.mark.parametrize("transport_out_sample_rate", [8000, 16000, 24000])
def test_create_speechify_tts_service_uses_transport_sample_rate(
    speechify_pipecat_stub,
    transport_out_sample_rate,
):
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.SPEECHIFY.value,
            api_key="test-key",
            model="simba-3.2",
            voice="geffen_32",
            language="en",
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=transport_out_sample_rate,
        transport_in_sample_rate=16000,
    )

    with (
        patch(
            "api.services.pipecat.speechify_tts.SpeechifyOwnedSessionTTSService"
        ) as mock_service,
        patch("api.services.pipecat.service_factory.aiohttp.ClientSession"),
    ):
        create_tts_service(user_config, audio_config)

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["aiohttp_session"] is not None
    assert kwargs["sample_rate"] == transport_out_sample_rate
    assert kwargs["settings"].voice == "geffen_32"
    assert kwargs["settings"].model == "simba-3.2"
    assert kwargs["settings"].language == Language.EN


def test_create_speechify_tts_service_converts_language(speechify_pipecat_stub):
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.SPEECHIFY.value,
            api_key="test-key",
            model="simba-3.0",
            voice="adriana",
            language="pt-BR",
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )

    with (
        patch(
            "api.services.pipecat.speechify_tts.SpeechifyOwnedSessionTTSService"
        ) as mock_service,
        patch("api.services.pipecat.service_factory.aiohttp.ClientSession"),
    ):
        create_tts_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    assert kwargs["settings"].language == Language.PT_BR


def test_create_speechify_tts_service_passes_custom_language_through(
    speechify_pipecat_stub,
):
    # The config allows custom language codes; ones the pipecat Language enum
    # doesn't model must reach the provider verbatim, not be replaced with
    # English.
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.SPEECHIFY.value,
            api_key="test-key",
            model="simba-3.2",
            voice="beatrice_32",
            language="en-ZA",
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )

    with (
        patch(
            "api.services.pipecat.speechify_tts.SpeechifyOwnedSessionTTSService"
        ) as mock_service,
        patch("api.services.pipecat.service_factory.aiohttp.ClientSession"),
    ):
        create_tts_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    assert kwargs["settings"].language == "en-ZA"


def test_speechify_is_registered_for_key_validation():
    validator = UserConfigurationValidator()
    assert ServiceProviders.SPEECHIFY.value in validator._validator_map


def test_speechify_key_validation_accepts_valid_key():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        assert validator._check_speechify_api_key("simba-3.2", "sk-valid-key") is True
    called_url = mock_get.call_args.args[0]
    assert called_url == "https://api.speechify.ai/v1/voices?limit=1"
    headers = mock_get.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer sk-valid-key"


def test_speechify_key_validation_treats_non_auth_errors_as_inconclusive():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 500
        assert validator._check_speechify_api_key("simba-3.2", "sk-valid-key") is True


def test_speechify_key_validation_treats_connection_errors_as_inconclusive():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.side_effect = httpx.ConnectError("connection failed")
        assert validator._check_speechify_api_key("simba-3.2", "sk-valid-key") is True


def test_speechify_key_validation_rejects_bad_key():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 401
        with pytest.raises(ValueError):
            validator._check_speechify_api_key("simba-3.2", "bad-key")


def test_speechify_key_validation_rejects_unauthorized_key():
    validator = UserConfigurationValidator()
    with patch("api.services.configuration.check_validity.httpx.get") as mock_get:
        mock_get.return_value.status_code = 403
        with pytest.raises(ValueError, match="authorization failed"):
            validator._check_speechify_api_key("simba-3.2", "unauthorized-key")


def test_speechify_voice_options_cover_every_model():
    # The voice dropdown is filtered per model via model_options; the API
    # rejects voices outside a model's allow-list with HTTP 400.
    assert set(SPEECHIFY_TTS_VOICES_BY_MODEL) == set(SPEECHIFY_TTS_MODELS)
    assert SPEECHIFY_TTS_VOICES_BY_MODEL["simba-3.2"] == ["beatrice_32", "geffen_32"]
    voice_extra = SpeechifyTTSConfiguration.model_fields["voice"].json_schema_extra
    assert voice_extra["model_options"] is SPEECHIFY_TTS_VOICES_BY_MODEL
    # The default voice must be valid for the default model.
    default_config = SpeechifyTTSConfiguration(api_key="test-key")
    assert default_config.voice in SPEECHIFY_TTS_VOICES_BY_MODEL[default_config.model]
