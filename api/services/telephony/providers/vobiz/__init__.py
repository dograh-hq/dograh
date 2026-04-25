"""Vobiz telephony provider package."""

from typing import Any, Dict

from api.services.pipecat.audio_config import AudioConfig
from api.services.telephony.registry import ProviderSpec, register

from .config import VobizConfigurationRequest, VobizConfigurationResponse
from .provider import VobizProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "vobiz",
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
    name="vobiz",
    provider_cls=VobizProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    audio_config=_AUDIO_CONFIG,
    config_request_cls=VobizConfigurationRequest,
    config_response_cls=VobizConfigurationResponse,
)


register(SPEC)


__all__ = [
    "SPEC",
    "VobizConfigurationRequest",
    "VobizConfigurationResponse",
    "VobizProvider",
    "create_transport",
]
