from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pipecat.services.elevenlabs.stt import CommitStrategy
from pipecat.transcriptions.language import Language

from api.services.configuration.options import (
    ELEVENLABS_STT_LANGUAGES,
    ELEVENLABS_STT_MODELS,
)
from api.services.configuration.registry import (
    ElevenlabsSTTConfiguration,
    ServiceProviders,
)
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.service_factory import (
    create_stt_service,
    create_tts_service,
    stt_uses_external_turns,
)


def _audio_config() -> AudioConfig:
    return AudioConfig(
        transport_in_sample_rate=16000,
        transport_out_sample_rate=16000,
    )


def _elevenlabs_config(
    language: str = "en",
    base_url: str = "https://api.elevenlabs.io",
) -> SimpleNamespace:
    return SimpleNamespace(
        stt=SimpleNamespace(
            provider=ServiceProviders.ELEVENLABS.value,
            api_key="test-key",
            model="scribe_v2_realtime",
            language=language,
            base_url=base_url,
        )
    )


def _elevenlabs_tts_config(base_url: str) -> SimpleNamespace:
    return SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.ELEVENLABS.value,
            api_key="test-key",
            model="eleven_flash_v2_5",
            voice="test-voice",
            speed=1.0,
            base_url=base_url,
        )
    )


def test_elevenlabs_stt_configuration_exposes_defaults_and_languages():
    config = ElevenlabsSTTConfiguration(api_key="test-key")
    language_schema = ElevenlabsSTTConfiguration.model_json_schema()["properties"][
        "language"
    ]

    assert config.provider == ServiceProviders.ELEVENLABS
    assert config.model == "scribe_v2_realtime"
    assert config.language == "en"
    assert config.base_url == "https://api.elevenlabs.io"
    assert ELEVENLABS_STT_MODELS == ("scribe_v2_realtime",)
    assert "auto" in ELEVENLABS_STT_LANGUAGES
    assert "es" in ELEVENLABS_STT_LANGUAGES
    assert language_schema["examples"] == list(ELEVENLABS_STT_LANGUAGES)


def test_elevenlabs_stt_uses_realtime_service_with_language_mapping():
    user_config = _elevenlabs_config(language="es")

    assert not stt_uses_external_turns(user_config)

    with patch(
        "api.services.pipecat.service_factory.ElevenLabsRealtimeSTTService"
    ) as stt_service:
        create_stt_service(user_config, _audio_config())

    stt_service.assert_called_once()
    kwargs = stt_service.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["base_url"] == "api.elevenlabs.io"
    assert kwargs["commit_strategy"] == CommitStrategy.VAD
    assert kwargs["sample_rate"] == 16000
    assert kwargs["should_interrupt"] is False
    assert kwargs["settings"].model == "scribe_v2_realtime"
    assert kwargs["settings"].language == Language.ES


def test_elevenlabs_stt_auto_language_passes_none():
    user_config = _elevenlabs_config(language="auto")

    with patch(
        "api.services.pipecat.service_factory.ElevenLabsRealtimeSTTService"
    ) as stt_service:
        create_stt_service(user_config, _audio_config())

    kwargs = stt_service.call_args.kwargs
    assert kwargs["settings"].language is None


def test_elevenlabs_stt_extracts_hostname_from_residency_base_url():
    user_config = _elevenlabs_config(base_url="https://api.eu.residency.elevenlabs.io")

    with patch(
        "api.services.pipecat.service_factory.ElevenLabsRealtimeSTTService"
    ) as stt_service:
        create_stt_service(user_config, _audio_config())

    kwargs = stt_service.call_args.kwargs
    assert kwargs["base_url"] == "api.eu.residency.elevenlabs.io"


def test_elevenlabs_stt_custom_language_passes_through():
    user_config = _elevenlabs_config(language="custom-lang")

    with patch(
        "api.services.pipecat.service_factory.ElevenLabsRealtimeSTTService"
    ) as stt_service:
        create_stt_service(user_config, _audio_config())

    kwargs = stt_service.call_args.kwargs
    assert kwargs["settings"].language == "custom-lang"


def test_elevenlabs_stt_bare_hostname_base_url_is_preserved():
    user_config = _elevenlabs_config(base_url="api.elevenlabs.io")

    with patch(
        "api.services.pipecat.service_factory.ElevenLabsRealtimeSTTService"
    ) as stt_service:
        create_stt_service(user_config, _audio_config())

    kwargs = stt_service.call_args.kwargs
    assert kwargs["base_url"] == "api.elevenlabs.io"


def test_elevenlabs_stt_preserves_non_default_port_in_base_url():
    user_config = _elevenlabs_config(base_url="https://localhost:8443")

    with patch(
        "api.services.pipecat.service_factory.ElevenLabsRealtimeSTTService"
    ) as stt_service:
        create_stt_service(user_config, _audio_config())

    kwargs = stt_service.call_args.kwargs
    assert kwargs["base_url"] == "localhost:8443"


def test_elevenlabs_stt_preserves_proxy_path_prefix_in_base_url():
    user_config = _elevenlabs_config(base_url="https://proxy.example.com/elevenlabs")

    with patch(
        "api.services.pipecat.service_factory.ElevenLabsRealtimeSTTService"
    ) as stt_service:
        create_stt_service(user_config, _audio_config())

    kwargs = stt_service.call_args.kwargs
    assert kwargs["base_url"] == "proxy.example.com/elevenlabs"


@pytest.mark.parametrize(
    ("base_url", "expected_url"),
    [
        (
            "https://api.eu.residency.elevenlabs.io/elevenlabs/",
            "wss://api.eu.residency.elevenlabs.io/elevenlabs",
        ),
        ("http://localhost:8000/", "ws://localhost:8000"),
    ],
)
def test_elevenlabs_tts_uses_normalized_websocket_url(base_url, expected_url):
    user_config = _elevenlabs_tts_config(base_url)

    with patch(
        "api.services.pipecat.service_factory.ElevenLabsTTSService"
    ) as tts_service:
        create_tts_service(user_config, _audio_config())

    assert tts_service.call_args.kwargs["url"] == expected_url


def test_elevenlabs_stt_listed_custom_language_maps_to_pipecat_enum():
    user_config = _elevenlabs_config(language="yue")

    with patch(
        "api.services.pipecat.service_factory.ElevenLabsRealtimeSTTService"
    ) as stt_service:
        create_stt_service(user_config, _audio_config())

    kwargs = stt_service.call_args.kwargs
    assert kwargs["settings"].language == Language.YUE
