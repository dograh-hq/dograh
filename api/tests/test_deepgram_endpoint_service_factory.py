"""The configured Deepgram endpoint has to reach the service that uses it.

Deepgram's regional hosts are the only control over which jurisdiction sees the
call audio, so a stored-but-ignored endpoint is not a cosmetic bug: it silently
transfers personal data out of the region an operator selected. These tests pin
the value all the way to the constructor argument for each of the three
Deepgram services, because each one takes a different shape of the same host.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from api.services.configuration.options import (
    DEEPGRAM_BASE_URLS,
    DEEPGRAM_DEFAULT_BASE_URL,
)
from api.services.configuration.registry import (
    DeepgramSTTConfiguration,
    DeepgramTTSConfiguration,
    ServiceProviders,
)
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.service_factory import (
    create_stt_service,
    create_tts_service,
)

EU = "https://api.eu.deepgram.com"


def _audio_config() -> AudioConfig:
    return AudioConfig(
        transport_in_sample_rate=16000,
        transport_out_sample_rate=16000,
    )


def _stt_config(model: str, base_url: str | None = None) -> SimpleNamespace:
    stt = SimpleNamespace(
        provider=ServiceProviders.DEEPGRAM.value,
        api_key="test-key",
        model=model,
        language="multi",
    )
    if base_url is not None:
        stt.base_url = base_url
    return SimpleNamespace(stt=stt)


def _tts_config(base_url: str | None = None) -> SimpleNamespace:
    tts = SimpleNamespace(
        provider=ServiceProviders.DEEPGRAM.value,
        api_key="test-key",
        model="aura-2",
        voice="aura-2-helena-en",
    )
    if base_url is not None:
        tts.base_url = base_url
    return SimpleNamespace(tts=tts)


def _build_stt(user_config, service: str):
    target = f"api.services.pipecat.service_factory.{service}"
    with patch(target) as mock:
        create_stt_service(user_config, _audio_config())
    mock.assert_called_once()
    return mock.call_args.kwargs


def _build_tts(user_config):
    with patch("api.services.pipecat.service_factory.DeepgramTTSService") as mock:
        create_tts_service(user_config, _audio_config())
    mock.assert_called_once()
    return mock.call_args.kwargs


# --- the configured endpoint reaches each service ----------------------------


def test_nova_receives_configured_endpoint():
    # DeepgramSTTService takes the host and derives wss:// and https:// itself.
    kwargs = _build_stt(_stt_config("nova-3-general", EU), "DeepgramSTTService")
    assert kwargs["base_url"] == EU


def test_flux_receives_configured_endpoint_as_a_socket_url():
    # Flux wants the fully-qualified socket URL including the v2 listen path.
    kwargs = _build_stt(_stt_config("flux-general-multi", EU), "DeepgramFluxSTTService")
    assert kwargs["url"] == "wss://api.eu.deepgram.com/v2/listen"


def test_tts_receives_configured_endpoint_without_a_path():
    # DeepgramTTSService appends /v1/speak, so it must not be given one.
    kwargs = _build_tts(_tts_config(EU))
    assert kwargs["base_url"] == "wss://api.eu.deepgram.com"


# --- defaults and tolerated input --------------------------------------------


@pytest.mark.parametrize(
    "model,service,key,expected",
    [
        ("nova-3-general", "DeepgramSTTService", "base_url", DEEPGRAM_DEFAULT_BASE_URL),
        (
            "flux-general-multi",
            "DeepgramFluxSTTService",
            "url",
            "wss://api.deepgram.com/v2/listen",
        ),
    ],
)
def test_unset_endpoint_falls_back_to_the_default_host(model, service, key, expected):
    # Older stored configurations predate the field; they must keep working.
    kwargs = _build_stt(_stt_config(model), service)
    assert kwargs[key] == expected


@pytest.mark.parametrize("configured", ["api.eu.deepgram.com", EU + "/", "  " + EU])
def test_bare_host_and_stray_characters_normalise(configured):
    # Deepgram documents the switch as a hostname swap, so that is what people
    # type. Accepting it is cheaper than a support round trip.
    kwargs = _build_stt(_stt_config("nova-3-general", configured), "DeepgramSTTService")
    assert kwargs["base_url"] == EU


def test_insecure_scheme_is_preserved_for_self_hosted_deepgram():
    # Self-hosted Deepgram on a private network is a supported deployment; do
    # not silently upgrade a deliberate ws:// to wss:// and break the connection.
    kwargs = _build_stt(
        _stt_config("flux-general-multi", "http://deepgram.internal:8080"),
        "DeepgramFluxSTTService",
    )
    assert kwargs["url"] == "ws://deepgram.internal:8080/v2/listen"


# --- the field is reachable from the dashboard -------------------------------


@pytest.mark.parametrize(
    "config_cls", [DeepgramSTTConfiguration, DeepgramTTSConfiguration]
)
def test_endpoint_is_offered_in_the_configuration_schema(config_cls):
    # The form renders straight from this schema, so the EU host has to be
    # listed here for an operator to be able to pick it without guessing.
    schema = config_cls.model_json_schema()["properties"]["base_url"]

    assert schema["default"] == DEEPGRAM_DEFAULT_BASE_URL
    assert EU in schema["examples"]
    assert schema["allow_custom_input"] is True
    assert list(DEEPGRAM_BASE_URLS) == schema["examples"]


@pytest.mark.parametrize(
    "config_cls,primary",
    [(DeepgramSTTConfiguration, "model"), (DeepgramTTSConfiguration, "voice")],
)
def test_endpoint_does_not_displace_the_primary_field(config_cls, primary):
    # The form promotes the first non-secret property into the slot beside the
    # provider picker. That slot belongs to the thing operators change often;
    # an endpoint they set once should sit with the rest of the settings.
    fields = [
        f
        for f in config_cls.model_json_schema()["properties"]
        if f not in ("provider", "api_key")
    ]

    assert fields[0] == primary
    assert fields.index("base_url") > fields.index(primary)
