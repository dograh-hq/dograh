import re
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from pipecat.services.elevenlabs.dialogue.tts import ElevenLabsDialogueTTSService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

from api.services.configuration.registry import ElevenlabsTTSConfiguration
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.service_factory import create_tts_service


@pytest.mark.parametrize(
    ("model", "service_class", "endpoint"),
    [
        ("eleven_v3", ElevenLabsDialogueTTSService, "text-to-dialogue"),
        ("eleven_v3_conversational", ElevenLabsDialogueTTSService, "text-to-dialogue"),
        ("eleven_v4", ElevenLabsDialogueTTSService, "text-to-dialogue"),
        ("eleven_v4_turbo", ElevenLabsDialogueTTSService, "text-to-dialogue"),
        ("eleven_v3_custom", ElevenLabsDialogueTTSService, "text-to-dialogue"),
        ("eleven_v4_custom", ElevenLabsDialogueTTSService, "text-to-dialogue"),
        ("eleven_flash_v2_5", ElevenLabsTTSService, "text-to-speech/test-voice"),
        ("eleven_multilingual_v2", ElevenLabsTTSService, "text-to-speech/test-voice"),
        ("eleven_turbo_v2_5", ElevenLabsTTSService, "text-to-speech/test-voice"),
    ],
)
def test_elevenlabs_models_use_matching_websocket_protocol(
    model, service_class, endpoint
):
    config = ElevenlabsTTSConfiguration(
        api_key="test-key",
        model=model,
        voice="Danish voice - test-voice",
        speed=1.2,
        base_url="https://api.eu.residency.elevenlabs.io/elevenlabs/",
    )
    service = create_tts_service(
        SimpleNamespace(tts=config),
        AudioConfig(transport_in_sample_rate=16000, transport_out_sample_rate=16000),
    )

    assert type(service) is service_class
    url = urlparse(service._build_websocket_url())
    assert url.scheme == "wss"
    assert url.netloc == "api.eu.residency.elevenlabs.io"
    assert url.path == f"/elevenlabs/v1/{endpoint}/multi-stream-input"
    assert parse_qs(url.query)["model_id"] == [model]
    assert service._settings.voice == "test-voice"
    speed = config.model_json_schema()["properties"]["speed"]
    speed_supported = next(
        (
            rule["supported"]
            for rule in speed["model_constraints"]
            if re.search(rule["pattern"], model)
        ),
        True,
    )
    assert speed_supported is (service_class is ElevenLabsTTSService)
    if service_class is ElevenLabsDialogueTTSService:
        assert service._voice_settings == {"stability": 0.8}
    else:
        assert service._voice_settings == {
            "stability": 0.8,
            "speed": 1.2,
            "similarity_boost": 0.75,
        }


def test_elevenlabs_model_selector_exposes_dialogue_models_without_speed_control():
    schema = ElevenlabsTTSConfiguration.model_json_schema()["properties"]
    dialogue_models = {
        "eleven_v3",
        "eleven_v3_conversational",
        "eleven_v4",
        "eleven_v4_turbo",
    }

    assert dialogue_models | {"eleven_turbo_v2_5"} <= set(schema["model"]["examples"])
    assert ElevenlabsTTSConfiguration(api_key="test-key").model == "eleven_flash_v2_5"
