from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pipecat.transcriptions.language import Language

from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.service_factory import create_tts_service


def _create(language):
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.RIME.value,
            api_key="test-key",
            model="arcana",
            voice="astra",
            speed=1.0,
            language=language,
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )
    with patch("api.services.pipecat.service_factory.RimeTTSService") as mock_service:
        create_tts_service(user_config, audio_config)
    return mock_service.call_args.kwargs["settings"].language


@pytest.mark.parametrize(
    "language, expected",
    [
        ("en", Language.EN),
        ("hi", Language.HI),
        ("ar", Language.AR),
        ("it", Language.IT),
        ("ja", Language.JA),
        ("pt", Language.PT),
    ],
)
def test_create_rime_tts_service_passes_selected_language(language, expected):
    assert _create(language) == expected


@pytest.mark.parametrize("language", [None, "", "not-a-language"])
def test_create_rime_tts_service_defaults_to_english(language):
    assert _create(language) == Language.EN
