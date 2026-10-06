from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pipecat.services.settings import NOT_GIVEN
from pipecat.transcriptions.language import Language

from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.service_factory import (
    create_stt_service,
    dograh_stt_uses_flux_language,
    stt_uses_external_turns,
)


def _audio_config() -> AudioConfig:
    return AudioConfig(
        transport_in_sample_rate=16000,
        transport_out_sample_rate=16000,
    )


def _dograh_config(language: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.DOGRAH.value,
            api_key="mps-key",
            model="default",
            language=language,
        )
    )


@pytest.mark.parametrize(
    ("language", "uses_flux"),
    [
        (None, True),
        ("multi", True),
        ("es", True),
        ("en-GB", True),
        ("pt-BR", True),
        ("fr-CA", True),
        ("es-419", True),
        ("EN-gb", True),
        ("ar", False),
        ("ar-SA", False),
        ("zh-CN", False),
        ("ko-KR", False),
    ],
)
def test_dograh_flux_routing_and_turn_strategies_agree(language, uses_flux):
    assert dograh_stt_uses_flux_language(language) is uses_flux
    assert stt_uses_external_turns(_dograh_config(language)) is uses_flux


def test_create_dograh_multi_uses_flux_service_without_language_hint():
    user_config = _dograh_config("multi")

    with (
        patch(
            "api.services.pipecat.service_factory.DograhFluxSTTService"
        ) as flux_service,
        patch("api.services.pipecat.service_factory.DograhSTTService") as stt_service,
    ):
        create_stt_service(user_config, _audio_config(), correlation_id="corr-123")

    flux_service.assert_called_once()
    stt_service.assert_not_called()
    kwargs = flux_service.call_args.kwargs
    assert kwargs["correlation_id"] == "corr-123"
    assert kwargs["settings"].model == "flux-general-multi"
    assert kwargs["settings"].language_hints is NOT_GIVEN


@pytest.mark.parametrize(
    ("language", "hint"),
    [("es", Language.ES), ("en-GB", Language.EN), ("pt-BR", Language.PT)],
)
def test_create_dograh_supported_language_uses_flux_service_with_hint(language, hint):
    user_config = _dograh_config(language)

    with (
        patch(
            "api.services.pipecat.service_factory.DograhFluxSTTService"
        ) as flux_service,
        patch("api.services.pipecat.service_factory.DograhSTTService") as stt_service,
    ):
        create_stt_service(user_config, _audio_config(), keyterms=["Dograh"])

    flux_service.assert_called_once()
    stt_service.assert_not_called()
    kwargs = flux_service.call_args.kwargs
    assert kwargs["settings"].model == "flux-general-multi"
    assert kwargs["settings"].language_hints == [hint]
    assert kwargs["settings"].keyterm == ["Dograh"]


@pytest.mark.parametrize("language", ["ar", "ar-SA", "zh-CN", "ko-KR"])
def test_create_dograh_unsupported_language_falls_back_to_standard_stt_service(
    language,
):
    user_config = _dograh_config(language)

    with (
        patch(
            "api.services.pipecat.service_factory.DograhFluxSTTService"
        ) as flux_service,
        patch("api.services.pipecat.service_factory.DograhSTTService") as stt_service,
    ):
        create_stt_service(
            user_config,
            _audio_config(),
            keyterms=["Dograh"],
            correlation_id="corr-123",
        )

    flux_service.assert_not_called()
    stt_service.assert_called_once()
    kwargs = stt_service.call_args.kwargs
    assert kwargs["correlation_id"] == "corr-123"
    assert kwargs["settings"].model == "default"
    assert kwargs["settings"].language == language
    assert kwargs["keyterms"] == ["Dograh"]
