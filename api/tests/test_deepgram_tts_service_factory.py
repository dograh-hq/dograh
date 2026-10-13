from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.registry import DeepgramTTSConfiguration
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.service_factory import create_tts_service


def _tts_settings(config):
    with patch("api.services.pipecat.service_factory.DeepgramTTSService") as service:
        create_tts_service(
            config,
            AudioConfig(
                transport_in_sample_rate=16000, transport_out_sample_rate=16000
            ),
        )
    service.assert_called_once()
    return service.call_args.kwargs["settings"]


@pytest.mark.parametrize("speed", [0.7, 0.9, 1.0, 1.25, 1.5, None])
def test_deepgram_speed_survives_saved_configuration_and_reaches_service(speed):
    config = EffectiveAIModelConfiguration.model_validate(
        {
            "tts": {
                "provider": "deepgram",
                "api_key": "test-key",
                "voice": "aura-2-estrella-es",
                "speed": speed,
            }
        }
    )
    restored = EffectiveAIModelConfiguration.model_validate_json(
        config.model_dump_json()
    )

    settings = _tts_settings(restored)

    assert settings.voice == "aura-2-estrella-es"
    assert settings.speed == speed


@pytest.mark.parametrize("speed", [0.69, 1.51, float("nan"), float("inf")])
def test_deepgram_rejects_invalid_speed(speed):
    with pytest.raises(ValidationError, match="speed"):
        DeepgramTTSConfiguration(api_key="test-key", speed=speed)


@pytest.mark.parametrize("legacy", [False, True])
def test_deepgram_keeps_provider_default_when_speed_is_absent(legacy):
    # An unset speed must remain None, including for voices without speed support.
    tts = {"provider": "deepgram", "api_key": "test-key", "voice": "aura-2-fabian-de"}
    config = (
        SimpleNamespace(tts=SimpleNamespace(**tts, model="aura-2"))
        if legacy
        else EffectiveAIModelConfiguration.model_validate({"tts": tts})
    )

    assert _tts_settings(config).speed is None


def test_deepgram_speed_is_exposed_as_optional_bounded_number_in_form_schema():
    schema = DeepgramTTSConfiguration.model_json_schema()
    speed = schema["properties"]["speed"]

    assert "speed" not in schema["required"]
    assert speed["default"] is None
    assert {"type": "number", "minimum": 0.7, "maximum": 1.5} in speed["anyOf"]
