from types import SimpleNamespace
from unittest.mock import patch

from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.options import SMALLEST_TTS_MODELS
from api.services.configuration.registry import (
    REGISTRY,
    ServiceProviders,
    ServiceType,
    SmallestAISTTConfiguration,
    SmallestAITTSConfiguration,
)
from api.services.pipecat.service_factory import create_tts_service


def test_smallest_tts_configuration_defaults_and_registry():
    config = SmallestAITTSConfiguration(api_key="test-key")

    assert config.provider == ServiceProviders.SMALLEST
    assert config.model == "lightning_v3.1_pro"
    assert config.voice == "kaitlyn"
    assert config.language == "en"
    assert config.speed == 1.0
    assert (
        REGISTRY[ServiceType.TTS][ServiceProviders.SMALLEST]
        is SmallestAITTSConfiguration
    )


def test_smallest_tts_only_supports_lightning_v3_1_pro():
    # lightning_v3.1 retires 2026-11-30; this integration isn't carrying a
    # backward-compat path for it, so it must not be a selectable model.
    assert SMALLEST_TTS_MODELS == ("lightning_v3.1_pro",)
    model_options = SmallestAITTSConfiguration.model_fields["voice"].json_schema_extra[
        "model_options"
    ]
    assert "lightning_v3.1" not in model_options


def test_smallest_stt_configuration_defaults_and_registry():
    config = SmallestAISTTConfiguration(api_key="test-key")

    assert config.provider == ServiceProviders.SMALLEST
    assert config.model == "pulse"
    assert config.language == "en"
    assert (
        REGISTRY[ServiceType.STT][ServiceProviders.SMALLEST]
        is SmallestAISTTConfiguration
    )


def test_validator_accepts_smallest_services():
    validator = UserConfigurationValidator()

    assert (
        validator._validate_service(
            SmallestAITTSConfiguration(api_key="test-key"),
            "tts",
        )
        == []
    )
    assert (
        validator._validate_service(
            SmallestAISTTConfiguration(api_key="test-key"),
            "stt",
        )
        == []
    )


def test_create_smallest_tts_service_normalizes_hyphenated_model_values():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.SMALLEST.value,
            api_key="test-key",
            model="lightning-v3.1-pro",
            voice="kaitlyn",
            language="en",
            speed=1.0,
        )
    )
    audio_config = SimpleNamespace(transport_in_sample_rate=16000)

    with patch(
        "api.services.pipecat.service_factory.SmallestTTSService"
    ) as mock_service:
        create_tts_service(user_config, audio_config)

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["settings"].model == "lightning_v3.1_pro"
