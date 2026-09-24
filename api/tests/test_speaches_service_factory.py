from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pipecat.services.speaches.tts import SpeachesTTSService
from pydantic import TypeAdapter, ValidationError

from api.services.configuration.registry import ServiceProviders, TTSConfig
from api.services.pipecat.service_factory import create_stt_service, create_tts_service


def test_create_speaches_stt_service_uses_http_base_url():
    user_config = SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.SPEACHES.value,
            base_url="http://localhost:9100/v1",
            api_key=None,
            model="Systran/faster-whisper-small",
            language="tr",
        )
    )
    audio_config = SimpleNamespace(transport_in_sample_rate=16000)

    with patch(
        "api.services.pipecat.service_factory.SpeachesSTTService"
    ) as mock_service:
        create_stt_service(user_config, audio_config)

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["base_url"] == "http://localhost:9100/v1"
    assert kwargs["api_key"] == "none"
    assert kwargs["settings"].model == "Systran/faster-whisper-small"
    assert kwargs["settings"].language == "tr"


def _speaches_tts_config(**overrides):
    return TypeAdapter(TTSConfig).validate_python(
        {
            "provider": ServiceProviders.SPEACHES.value,
            "base_url": "http://localhost:8000/v1",
            "model": "voxcpm2",
            "voice": "default",
            **overrides,
        }
    )


def test_speaches_tts_configuration_round_trips_sample_rate():
    config = _speaches_tts_config(sample_rate=48000)

    assert config.sample_rate == 48000
    reloaded = _speaches_tts_config(**config.model_dump(exclude={"provider"}))
    assert reloaded.sample_rate == 48000


def test_speaches_tts_configuration_sample_rate_defaults_to_none():
    assert _speaches_tts_config().sample_rate is None
    # The dashboard form submits an empty string for a blank optional field.
    assert _speaches_tts_config(sample_rate="").sample_rate is None


def test_speaches_tts_configuration_rejects_unsupported_sample_rate():
    with pytest.raises(ValidationError, match="sample_rate"):
        _speaches_tts_config(sample_rate=12345)


def test_speaches_tts_configuration_rejects_non_numeric_sample_rate():
    with pytest.raises(ValidationError, match="sample_rate must be one of"):
        _speaches_tts_config(sample_rate="fast")


@pytest.mark.parametrize(
    ("overrides", "expected_sample_rate"),
    [({"sample_rate": 48000}, 48000), ({}, 24000)],
)
def test_create_speaches_tts_service_uses_configured_sample_rate(
    overrides, expected_sample_rate
):
    user_config = SimpleNamespace(tts=_speaches_tts_config(**overrides))

    service = create_tts_service(user_config, SimpleNamespace())

    assert isinstance(service, SpeachesTTSService)
    assert service._init_sample_rate == expected_sample_rate
