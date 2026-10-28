from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from pipecat.transcriptions.language import Language
from pydantic import TypeAdapter, ValidationError

from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.options import (
    SONIOX_REGIONS,
    SONIOX_TTS_LANGUAGES,
    SONIOX_TTS_MODELS,
    soniox_host,
)
from api.services.configuration.registry import (
    ServiceProviders,
    SonioxSTTConfiguration,
    SonioxTTSConfiguration,
    STTConfig,
    TTSConfig,
)
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.service_factory import create_stt_service, create_tts_service


def _soniox_tts_config(**tts) -> SimpleNamespace:
    config = TypeAdapter(TTSConfig).validate_python(
        {"provider": "soniox", "api_key": "test-key", **tts}
    )
    return SimpleNamespace(tts=config)


def test_soniox_tts_configuration_exposes_defaults():
    config = SonioxTTSConfiguration(api_key="test-key")

    assert config.provider == ServiceProviders.SONIOX
    assert config.model == "tts-rt-v2"
    assert config.voice == "Daniel"
    assert config.language == "en"
    assert config.speed == 1.0
    assert config.region == "us"
    assert SONIOX_TTS_MODELS == ("tts-rt-v2", "tts-rt-v1")
    assert "multi" not in SONIOX_TTS_LANGUAGES
    assert {"hi", "mr"} <= set(SONIOX_TTS_LANGUAGES)


def test_soniox_tts_config_parses_from_tts_union():
    user_config = _soniox_tts_config(voice="Madhura", language="mr", region="in")

    assert isinstance(user_config.tts, SonioxTTSConfiguration)
    assert user_config.tts.voice == "Madhura"
    assert user_config.tts.region == "in"


def test_soniox_tts_builds_service_for_default_region():
    user_config = _soniox_tts_config(voice="Madhura", language="mr")

    with patch("api.services.pipecat.service_factory.SonioxTTSService") as tts_service:
        create_tts_service(user_config, audio_config=None)

    tts_service.assert_called_once()
    kwargs = tts_service.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["url"] == "wss://tts-rt.soniox.com/tts-websocket"
    assert kwargs["settings"].model == "tts-rt-v2"
    assert kwargs["settings"].voice == "Madhura"
    assert kwargs["settings"].language == Language.MR
    # 1.0 is left unset so Soniox applies its own default.
    assert kwargs["settings"].speed is None
    assert kwargs["text_filters"]


def test_soniox_tts_uses_regional_endpoint_and_speed():
    user_config = _soniox_tts_config(region="in", speed=1.2)

    with patch("api.services.pipecat.service_factory.SonioxTTSService") as tts_service:
        create_tts_service(user_config, audio_config=None)

    kwargs = tts_service.call_args.kwargs
    assert kwargs["url"] == "wss://tts-rt.in.soniox.com/tts-websocket"
    assert kwargs["settings"].speed == 1.2


def test_soniox_stt_uses_regional_endpoint():
    config = TypeAdapter(STTConfig).validate_python(
        {"provider": "soniox", "api_key": "test-key", "region": "eu"}
    )
    audio_config = AudioConfig(
        transport_in_sample_rate=16000, transport_out_sample_rate=16000
    )

    with patch("api.services.pipecat.service_factory.SonioxSTTService") as stt_service:
        create_stt_service(SimpleNamespace(stt=config), audio_config)

    kwargs = stt_service.call_args.kwargs
    assert kwargs["url"] == "wss://stt-rt.eu.soniox.com/transcribe-websocket"


@pytest.mark.parametrize(
    ("region", "expected"),
    [
        (None, "api.soniox.com"),
        ("us", "api.soniox.com"),
        ("EU", "api.eu.soniox.com"),
        ("jp", "api.jp.soniox.com"),
        (" in ", "api.in.soniox.com"),
    ],
)
def test_soniox_host_maps_regions(region, expected):
    assert soniox_host("api", region) == expected


@pytest.mark.parametrize(
    "config_class", [SonioxSTTConfiguration, SonioxTTSConfiguration]
)
def test_soniox_region_is_validated_and_normalized(config_class):
    assert config_class(api_key="test-key", region="IN").region == "in"
    assert set(SONIOX_REGIONS) == {"us", "eu", "jp", "in"}

    with pytest.raises(ValidationError):
        config_class(api_key="test-key", region="mars")


def _soniox_models_response(status_code: int, url: str) -> httpx.Response:
    return httpx.Response(status_code, request=httpx.Request("GET", url))


@pytest.mark.parametrize(
    "config_class", [SonioxSTTConfiguration, SonioxTTSConfiguration]
)
@pytest.mark.parametrize(
    ("region", "url"),
    [
        ("us", "https://api.soniox.com/v1/models"),
        ("eu", "https://api.eu.soniox.com/v1/models"),
        ("jp", "https://api.jp.soniox.com/v1/models"),
        ("in", "https://api.in.soniox.com/v1/models"),
    ],
)
def test_soniox_connection_key_is_checked_in_its_region(config_class, region, url):
    config = config_class(api_key="test-key", region=region)

    with patch(
        "api.services.configuration.check_validity.httpx.get",
        return_value=_soniox_models_response(200, url),
    ) as get:
        UserConfigurationValidator().validate_connection(config)

    get.assert_called_once()
    assert get.call_args.args[0] == url
    assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer test-key"}


@pytest.mark.parametrize(
    "config_class", [SonioxSTTConfiguration, SonioxTTSConfiguration]
)
def test_soniox_connection_rejects_key_from_another_region(config_class):
    config = config_class(api_key="test-key", region="in")
    url = "https://api.in.soniox.com/v1/models"

    with patch(
        "api.services.configuration.check_validity.httpx.get",
        return_value=_soniox_models_response(401, url),
    ):
        with pytest.raises(ValueError, match=r"rejected by api\.in\.soniox\.com"):
            UserConfigurationValidator().validate_connection(config)
