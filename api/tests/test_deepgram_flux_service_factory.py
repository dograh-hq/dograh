from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pipecat.services.settings import NOT_GIVEN
from pipecat.transcriptions.language import Language

from pydantic import ValidationError

from api.services.configuration.registry import (
    DeepgramSTTConfiguration,
    ServiceProviders,
)
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.service_factory import create_stt_service


def test_deepgram_stt_schema_includes_flux_multilingual_language_options():
    language_schema = DeepgramSTTConfiguration.model_json_schema()["properties"][
        "language"
    ]

    assert "flux-general-multi" in language_schema["model_options"]
    assert "multi" in language_schema["model_options"]["flux-general-multi"]
    assert "es" in language_schema["model_options"]["flux-general-multi"]


@pytest.mark.parametrize(
    ("language", "hint"),
    [("es", Language.ES), ("en-GB", Language.EN), ("pt-BR", Language.PT)],
)
def test_create_deepgram_flux_multi_uses_flux_service_with_language_hint(
    language, hint
):
    user_config = SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.DEEPGRAM.value,
            api_key="test-key",
            model="flux-general-multi",
            language=language,
        )
    )
    audio_config = AudioConfig(
        transport_in_sample_rate=16000,
        transport_out_sample_rate=16000,
    )

    with patch(
        "api.services.pipecat.service_factory.DeepgramFluxSTTService"
    ) as mock_service:
        create_stt_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    assert kwargs["settings"].model == "flux-general-multi"
    assert kwargs["settings"].language_hints == [hint]


def test_create_deepgram_flux_multi_omits_auto_detect_language_hint():
    user_config = SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.DEEPGRAM.value,
            api_key="test-key",
            model="flux-general-multi",
            language="multi",
        )
    )
    audio_config = AudioConfig(
        transport_in_sample_rate=16000,
        transport_out_sample_rate=16000,
    )

    with patch(
        "api.services.pipecat.service_factory.DeepgramFluxSTTService"
    ) as mock_service:
        create_stt_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    assert kwargs["settings"].model == "flux-general-multi"
    assert kwargs["settings"].language_hints is NOT_GIVEN


def test_create_deepgram_flux_uses_custom_eot_params_when_set():
    user_config = SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.DEEPGRAM.value,
            api_key="test-key",
            model="flux-general-en",
            language="en",
            eot_timeout_ms=5000,
            eot_threshold=0.9,
            eager_eot_threshold=0.3,
        )
    )
    audio_config = AudioConfig(
        transport_in_sample_rate=16000,
        transport_out_sample_rate=16000,
    )

    with patch(
        "api.services.pipecat.service_factory.DeepgramFluxSTTService"
    ) as mock_service:
        create_stt_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    assert kwargs["settings"].eot_timeout_ms == 5000
    assert kwargs["settings"].eot_threshold == 0.9
    assert kwargs["settings"].eager_eot_threshold == 0.3


def test_create_deepgram_flux_defaults_eot_params_when_unset():
    user_config = SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.DEEPGRAM.value,
            api_key="test-key",
            model="flux-general-en",
            language="en",
        )
    )
    audio_config = AudioConfig(
        transport_in_sample_rate=16000,
        transport_out_sample_rate=16000,
    )

    with patch(
        "api.services.pipecat.service_factory.DeepgramFluxSTTService"
    ) as mock_service:
        create_stt_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    defaults = DeepgramSTTConfiguration.model_fields
    assert kwargs["settings"].eot_timeout_ms == defaults["eot_timeout_ms"].default
    assert kwargs["settings"].eot_threshold == defaults["eot_threshold"].default
    assert kwargs["settings"].eager_eot_threshold == defaults["eager_eot_threshold"].default
    
    
def test_deepgram_stt_rejects_out_of_range_eot_threshold():
    with pytest.raises(ValidationError):
        DeepgramSTTConfiguration(
            provider=ServiceProviders.DEEPGRAM,
            api_key="test-key",
            model="flux-general-en",
            eot_threshold=0.95,
        )


def test_deepgram_stt_rejects_eager_eot_threshold_above_eot_threshold():
    with pytest.raises(ValidationError):
        DeepgramSTTConfiguration(
            provider=ServiceProviders.DEEPGRAM,
            api_key="test-key",
            model="flux-general-en",
            eot_threshold=0.6,
            eager_eot_threshold=0.7,
        )