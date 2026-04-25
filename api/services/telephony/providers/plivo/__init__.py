"""Plivo telephony provider package."""

from typing import Any, Dict

from api.services.pipecat.audio_config import AudioConfig
from api.services.telephony.registry import ProviderSpec, register

from .config import PlivoConfigurationRequest, PlivoConfigurationResponse
from .provider import PlivoProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "plivo",
        "auth_id": value.get("auth_id"),
        "auth_token": value.get("auth_token"),
        "from_numbers": value.get("from_numbers", []),
    }


_AUDIO_CONFIG = AudioConfig(
    transport_in_sample_rate=8000,
    transport_out_sample_rate=8000,
    vad_sample_rate=8000,
    pipeline_sample_rate=8000,
    buffer_size_seconds=5.0,
)


SPEC = ProviderSpec(
    name="plivo",
    provider_cls=PlivoProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    audio_config=_AUDIO_CONFIG,
    config_request_cls=PlivoConfigurationRequest,
    config_response_cls=PlivoConfigurationResponse,
)


register(SPEC)


__all__ = [
    "SPEC",
    "PlivoConfigurationRequest",
    "PlivoConfigurationResponse",
    "PlivoProvider",
    "create_transport",
]
