from types import SimpleNamespace
from unittest.mock import patch

import pytest
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


def _create_flux_multi(language: str, language_hints: list[str]):
    config = DeepgramSTTConfiguration(
        api_key="test-key",
        model="flux-general-multi",
        language=language,
        language_hints=language_hints,
    )
    audio_config = AudioConfig(
        transport_in_sample_rate=16000,
        transport_out_sample_rate=16000,
    )
    with patch(
        "api.services.pipecat.service_factory.DeepgramFluxSTTService"
    ) as mock_service:
        create_stt_service(SimpleNamespace(stt=config), audio_config)
    return mock_service.call_args.kwargs["settings"]


def test_deepgram_stt_schema_shows_language_hints_only_for_flux_multi():
    hints_schema = DeepgramSTTConfiguration.model_json_schema()["properties"][
        "language_hints"
    ]

    assert hints_schema["type"] == "array"
    assert hints_schema["visible_for_models"] == ["flux-general-multi"]
    assert "hi" in hints_schema["examples"]


def test_create_deepgram_flux_multi_sends_every_language_hint():
    settings = _create_flux_multi("multi", ["hi", "en"])

    assert settings.language_hints == [Language.HI, Language.EN]


def test_create_deepgram_flux_multi_combines_language_with_hints_once():
    settings = _create_flux_multi("hi", ["en", "hi"])

    assert settings.language_hints == [Language.HI, Language.EN]


def test_deepgram_stt_rejects_language_hints_flux_does_not_support():
    with pytest.raises(ValueError, match="ta"):
        DeepgramSTTConfiguration(
            api_key="test-key",
            model="flux-general-multi",
            language_hints=["en", "ta"],
        )
