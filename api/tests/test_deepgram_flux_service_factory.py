from types import SimpleNamespace
from unittest.mock import patch

from pipecat.services.settings import NOT_GIVEN
from pipecat.transcriptions.language import Language

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


def test_create_deepgram_flux_multi_uses_flux_service_with_language_hint():
    user_config = SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.DEEPGRAM.value,
            api_key="test-key",
            model="flux-general-multi",
            language="es",
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
    assert kwargs["settings"].language_hints == [Language.ES]


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
