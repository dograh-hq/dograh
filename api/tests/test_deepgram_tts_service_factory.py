from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pipecat.services.tts_service import TextAggregationMode
from pydantic import ValidationError

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.registry import DeepgramTTSConfiguration
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.service_factory import create_tts_service

AURA = "api.services.pipecat.service_factory.DeepgramTTSService"
FLUX = "api.services.pipecat.service_factory.DeepgramFluxTTSService"


def _create(config):
    create_tts_service(
        config,
        AudioConfig(transport_in_sample_rate=16000, transport_out_sample_rate=16000),
    )


def _tts_settings(config):
    with patch(AURA) as aura, patch(FLUX) as flux:
        _create(config)
    flux.assert_not_called()
    aura.assert_called_once()
    return aura.call_args.kwargs["settings"]


def _flux_tts_kwargs(config):
    with patch(AURA) as aura, patch(FLUX) as flux:
        _create(config)
    aura.assert_not_called()
    flux.assert_called_once()
    return flux.call_args.kwargs


def _flux_config(**tts):
    return EffectiveAIModelConfiguration.model_validate(
        {
            "tts": {
                "provider": "deepgram",
                "api_key": "test-key",
                "voice": "flux-maeve-en",
                **tts,
            }
        }
    )


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


# --- Flux voices ---------------------------------------------------------------


def test_flux_voice_is_routed_to_the_flux_service_on_the_v2_endpoint():
    # Deepgram serves Flux voices only from /v2/speak; the Aura service would
    # be rejected with an HTTP 400 at the websocket handshake.
    restored = EffectiveAIModelConfiguration.model_validate_json(
        _flux_config(speed=1.1, expressivity=1).model_dump_json()
    )

    kwargs = _flux_tts_kwargs(restored)

    assert kwargs["url"] == "wss://api.deepgram.com/v2/speak"
    assert kwargs["settings"].voice == "flux-maeve-en"
    assert kwargs["settings"].speed == 1.1
    assert kwargs["settings"].expressivity == 1
    # The function-tag filter needs a whole tag, so tokens are not streamed raw.
    assert kwargs["text_aggregation_mode"] is TextAggregationMode.SENTENCE
    assert kwargs["text_filters"]


def test_flux_voice_leaves_unset_tuning_at_the_provider_default():
    settings = _flux_tts_kwargs(_flux_config())["settings"]

    assert settings.speed is None
    assert settings.expressivity is None


def test_legacy_configuration_without_expressivity_reaches_the_flux_service():
    # Stored configurations predate the field and arrive as plain attributes.
    tts = SimpleNamespace(
        provider="deepgram", api_key="test-key", voice="flux-maeve-en", model="flux"
    )

    settings = _flux_tts_kwargs(SimpleNamespace(tts=tts))["settings"]

    assert settings.voice == "flux-maeve-en"
    assert settings.expressivity is None


@pytest.mark.parametrize("expressivity", [-3, 3, 0.5, "animated"])
def test_deepgram_rejects_invalid_expressivity(expressivity):
    with pytest.raises(ValidationError, match="expressivity"):
        DeepgramTTSConfiguration(
            api_key="test-key", voice="flux-maeve-en", expressivity=expressivity
        )


@pytest.mark.parametrize(
    "voice,model",
    [
        ("flux-maeve-en", "flux"),
        ("aura-2-helena-en", "aura-2"),
        ("aura-asteria-en", "aura-1"),
        # Saved settings predate the model field and carry only the voice.
        ("custom-voice", "aura-2"),
    ],
)
def test_model_is_inferred_from_the_voice_prefix(voice, model):
    assert DeepgramTTSConfiguration(api_key="test-key", voice=voice).model == model


@pytest.mark.parametrize(
    "model,voice",
    [
        ("flux", "flux-heather-en"),
        ("aura-1", "aura-asteria-en"),
        ("aura-2", "aura-2-helena-en"),
    ],
)
@pytest.mark.parametrize("cleared", [None, ""])
def test_model_without_a_voice_takes_the_family_default(model, voice, cleared):
    # The form clears the voice when the model changes; saving straight away
    # must still produce a voice the chosen endpoint accepts.
    kwargs = {} if cleared is None else {"voice": cleared}
    config = DeepgramTTSConfiguration(api_key="test-key", model=model, **kwargs)

    assert config.voice == voice


@pytest.mark.parametrize(
    "settings,field",
    [
        ({"voice": 123}, "voice"),
        ({"voice": ["aura-2-helena-en"]}, "voice"),
        ({"model": ["aura-2"]}, "model"),
        ({"model": []}, "model"),
        ({"model": 2}, "model"),
        ({"model": "aura-3"}, "model"),
        ({"model": "", "voice": "aura-2-helena-en"}, "model"),
    ],
)
def test_malformed_input_is_a_field_error_not_a_crash(settings, field):
    # The before-validator completes well-typed halves only; anything else must
    # surface as a 422 naming the field, never as an exception escaping it.
    with pytest.raises(ValidationError, match=field):
        DeepgramTTSConfiguration(api_key="test-key", **settings)


def test_voice_from_another_family_is_rejected():
    with pytest.raises(ValidationError, match="voice"):
        DeepgramTTSConfiguration(
            api_key="test-key", model="flux", voice="aura-2-helena-en"
        )


def test_unrecognised_custom_voice_keeps_the_chosen_model():
    config = DeepgramTTSConfiguration(
        api_key="test-key", model="flux", voice="my-cloned-voice"
    )

    assert config.model == "flux"
    assert config.voice == "my-cloned-voice"


@pytest.mark.parametrize(
    "voice,supported",
    [
        ("aura-2-helena-en", True),
        ("aura-2-estrella-es", True),
        ("flux-maeve-en", True),
        ("aura-2-leda-nl", False),
        ("aura-asteria-en", False),
    ],
)
def test_speed_is_only_accepted_where_the_provider_supports_it(voice, supported):
    # Deepgram rejects the handshake outright for other voices, so a saved
    # value would only ever fail at call time.
    def build():
        return DeepgramTTSConfiguration(api_key="test-key", voice=voice, speed=1.2)

    if supported:
        assert build().speed == 1.2
    else:
        with pytest.raises(ValidationError, match="speed"):
            build()
        assert DeepgramTTSConfiguration(api_key="test-key", voice=voice).speed is None


def test_model_is_the_primary_field_and_gates_the_other_settings():
    schema = DeepgramTTSConfiguration.model_json_schema()
    fields = [f for f in schema["properties"] if f not in ("provider", "api_key")]

    assert fields[0] == "model"
    assert schema["properties"]["model"]["enum"] == ["aura-2", "aura-1", "flux"]
    assert schema["properties"]["voice"]["reset_on_change"] == ["model"]
    assert schema["properties"]["expressivity"]["visible_for_models"] == ["flux"]
    assert schema["properties"]["speed"]["model_constraints"] == [
        {"pattern": "^aura-1$", "supported": False}
    ]


def test_deepgram_expressivity_is_exposed_as_optional_choice_in_form_schema():
    schema = DeepgramTTSConfiguration.model_json_schema()
    expressivity = schema["properties"]["expressivity"]

    assert "expressivity" not in schema["required"]
    assert expressivity["default"] is None
    assert {"enum": [-2, -1, 0, 1, 2], "type": "integer"} in expressivity["anyOf"]
