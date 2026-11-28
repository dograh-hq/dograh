import importlib.util
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from pipecat.transcriptions.language import Language

from api.constants import APP_VERSION
from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import (
    SPEECHIFY_SIMBA_35_LANGUAGES,
    SPEECHIFY_TTS_LANGUAGES_BY_MODEL,
    SPEECHIFY_TTS_MODELS,
    SPEECHIFY_TTS_VOICES,
    SPEECHIFY_TTS_VOICES_BY_MODEL,
    ServiceProviders,
    SpeechifyTTSConfiguration,
)
from api.services.pipecat.service_factory import create_tts_service


def _speechify_pipecat_module_present() -> bool:
    try:
        return importlib.util.find_spec("pipecat.services.speechify.tts") is not None
    except ModuleNotFoundError:
        return False


@pytest.fixture
def speechify_pipecat_stub(monkeypatch):
    """Provide pipecat.services.speechify.tts for the factory's lazy import.

    Stubs the module only when the pinned pipecat checkout predates upstream's
    SpeechifyHttpTTSService. Once the submodule includes the real module, the
    tests run against the real classes so signature drift fails loudly.
    """
    if _speechify_pipecat_module_present():
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
    assert config.model == "simba-3.5"
    assert config.voice == "beatrice_32"
    assert config.language == "en"
    assert SPEECHIFY_TTS_MODELS[0] == "simba-3.5"
    assert "beatrice_32" in SPEECHIFY_TTS_VOICES


def test_speechify_language_options_cover_every_model():
    # The language dropdown is filtered per model via model_options; every
    # selectable model needs an entry, and simba-3.2 is documented English-only.
    assert set(SPEECHIFY_TTS_LANGUAGES_BY_MODEL) == set(SPEECHIFY_TTS_MODELS)
    assert SPEECHIFY_TTS_LANGUAGES_BY_MODEL["simba-3.2"] == ["en"]
    assert SPEECHIFY_TTS_LANGUAGES_BY_MODEL["simba-3.5"] is SPEECHIFY_SIMBA_35_LANGUAGES
    language_extra = SpeechifyTTSConfiguration.model_fields[
        "language"
    ].json_schema_extra
    assert language_extra["model_options"] is SPEECHIFY_TTS_LANGUAGES_BY_MODEL


def test_speechify_simba_35_languages_resolve_through_pipecat():
    # simba-3.5 is documented with 48 languages under one model id. Every
    # listed code must be a pipecat Language so the factory converts it and
    # pipecat resolves it to the code Speechify receives, instead of the
    # "unrecognized code" pass-through path meant for manual entries.
    assert len(SPEECHIFY_SIMBA_35_LANGUAGES) == 48
    assert len(set(SPEECHIFY_SIMBA_35_LANGUAGES)) == 48
    assert SPEECHIFY_SIMBA_35_LANGUAGES[0] == "en"
    for code in SPEECHIFY_SIMBA_35_LANGUAGES:
        Language(code)


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


def test_create_speechify_tts_service_converts_simba_35_language(
    speechify_pipecat_stub,
):
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.SPEECHIFY.value,
            api_key="test-key",
            model="simba-3.5",
            voice="alicia",
            language="ja",
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
    assert kwargs["settings"].model == "simba-3.5"
    assert kwargs["settings"].language == Language.JA


@pytest.mark.skipif(
    not _speechify_pipecat_module_present(),
    reason="pinned pipecat lacks the Speechify module",
)
def test_speechify_service_sends_simba_35_language_code():
    # pipecat's Speechify language map only covers the simba-3.0 locales; a
    # simba-3.5 language outside it must still reach the request body as its
    # own code rather than being dropped (which would make Speechify fall back
    # to the voice's default language).
    from pipecat.services.speechify.tts import SpeechifyTTSSettings

    from api.services.pipecat.speechify_tts import SpeechifyOwnedSessionTTSService

    service = SpeechifyOwnedSessionTTSService(
        api_key="test-key",
        aiohttp_session=MagicMock(),
        sample_rate=24000,
        settings=SpeechifyTTSSettings(
            voice="alicia", model="simba-3.5", language=Language.JA
        ),
    )

    payload = service._build_payload("こんにちは")
    assert payload["model"] == "simba-3.5"
    assert payload["voice_id"] == "alicia"
    assert payload["language"] == "ja"


@pytest.mark.skipif(
    not _speechify_pipecat_module_present(),
    reason="pinned pipecat lacks the Speechify module",
)
def test_speechify_service_attributes_requests_to_dograh():
    # pipecat names itself in Speechify's caller headers; the wrapper must
    # replace that with Dograh while keeping the auth header intact.
    from pipecat.services.speechify.tts import SpeechifyTTSSettings

    from api.services.pipecat.speechify_tts import SpeechifyOwnedSessionTTSService

    service = SpeechifyOwnedSessionTTSService(
        api_key="test-key",
        aiohttp_session=MagicMock(),
        sample_rate=24000,
        settings=SpeechifyTTSSettings(voice="alicia", model="simba-3.5"),
    )

    assert service._headers["Authorization"] == "Bearer test-key"
    assert service._headers["Speechify-Caller"] == "dograh"
    assert service._headers["Speechify-Caller-Version"] == APP_VERSION


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


@pytest.mark.skipif(
    _speechify_pipecat_module_present(),
    reason="pinned pipecat ships the Speechify module",
)
def test_create_speechify_tts_service_reports_missing_pipecat_module():
    # Selecting Speechify on a pipecat checkout without the service must fail
    # with an explicit configuration error, not a bare ModuleNotFoundError.
    from fastapi import HTTPException

    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.SPEECHIFY.value,
            api_key="test-key",
            model="simba-3.2",
            voice="beatrice_32",
            language="en",
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=16000,
        transport_in_sample_rate=16000,
    )

    with pytest.raises(HTTPException) as exc_info:
        create_tts_service(user_config, audio_config)
    assert "pipecat build" in exc_info.value.detail


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
    # Speechify credits usage to the integration named in this pair.
    assert headers["Speechify-Caller"] == "dograh"
    assert headers["Speechify-Caller-Version"] == APP_VERSION


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
    # The voice dropdown is filtered per model via model_options; a voice
    # must list the model in GET /v1/voices or synthesis fails. Every shipped
    # voice lists all three models, so each model offers the full set.
    assert set(SPEECHIFY_TTS_VOICES_BY_MODEL) == set(SPEECHIFY_TTS_MODELS)
    for model in SPEECHIFY_TTS_MODELS:
        assert SPEECHIFY_TTS_VOICES_BY_MODEL[model] == SPEECHIFY_TTS_VOICES
    voice_extra = SpeechifyTTSConfiguration.model_fields["voice"].json_schema_extra
    assert voice_extra["model_options"] is SPEECHIFY_TTS_VOICES_BY_MODEL
    # The default voice must be valid for the default model.
    default_config = SpeechifyTTSConfiguration(api_key="test-key")
    assert default_config.voice in SPEECHIFY_TTS_VOICES_BY_MODEL[default_config.model]
