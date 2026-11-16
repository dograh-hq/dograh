from types import SimpleNamespace

import pytest
from gnani.tts import SUPPORTED_TTS_LANGUAGES, TIMBRE_V25_VOICES, TIMBRE_V30_VOICES
from pipecat_gnani import GnaniTTSService
from pydantic import TypeAdapter, ValidationError

from api.services.configuration.model_connections import model_connection_catalog
from api.services.configuration.registry import TTSConfig
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.service_factory import create_tts_service


def gnani_config(**settings):
    return TypeAdapter(TTSConfig).validate_python(
        {"provider": "gnani", "api_key": " test-key ", **settings}
    )


def test_picker_matches_sdk_and_model_specific_defaults():
    props = model_connection_catalog()["services"]["tts"]["gnani"]["settings_schema"][
        "properties"
    ]
    assert props["model"]["enum"] == ["timbre-v2.5", "timbre-v3.0"]
    assert set(props["language"]["enum"]) == SUPPORTED_TTS_LANGUAGES
    assert props["speed"]["minimum"] == 0.85
    assert props["speed"]["maximum"] == 1.15
    default = gnani_config()
    assert (default.model, default.voice, default.language, default.speed) == (
        "timbre-v2.5",
        "Pranav",
        "auto",
        1.0,
    )
    for model, voices, first in [
        ("timbre-v2.5", TIMBRE_V25_VOICES, "Pranav"),
        ("timbre-v3.0", TIMBRE_V30_VOICES, "Avinash"),
    ]:
        choices = props["voice"]["model_options"][model]
        assert set(choices) == voices
        assert choices[0] == gnani_config(model=model).voice == first


@pytest.mark.parametrize(
    "settings",
    [
        {"model": "timbre-v2.0"},
        {"voice": "pranav"},
        {"voice": "Avinash"},
        {"model": "timbre-v3.0", "voice": "Pranav"},
        {"language": "hi-en"},
        {"speed": 0.84},
        {"speed": 1.16},
    ],
)
def test_invalid_settings(settings):
    with pytest.raises(ValidationError):
        gnani_config(**settings)


@pytest.mark.parametrize(
    ("model", "voice", "language", "speed", "rate"),
    [
        ("timbre-v2.5", "Poorvi", "auto", 0.85, 8000),
        ("timbre-v3.0", "Shivani", "ta-IN", 1.15, 16000),
    ],
)
def test_saved_selection_reaches_streaming_factory(model, voice, language, speed, rate):
    config = gnani_config(model=model, voice=voice, language=language, speed=speed)
    saved = TypeAdapter(TTSConfig).validate_json(config.model_dump_json())
    service = create_tts_service(SimpleNamespace(tts=saved), AudioConfig(rate, rate))
    assert isinstance(service, GnaniTTSService)
    assert service._api_key == "test-key"
    assert service._init_sample_rate == rate
    assert (
        service._settings.model,
        service._settings.voice,
        service._settings.language,
        service._settings.speed,
    ) == (model, voice, language, speed)
    assert service._skip_text_types == ["recording_router", "recording"]
    assert service._silence_time_s == 1.0
    assert service._ws is None
