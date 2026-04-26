"""Telnyx telephony provider package."""

from typing import Any, Dict

from api.services.pipecat.audio_config import AudioConfig
from api.services.telephony.registry import ProviderSpec, register

from .config import TelnyxConfigurationRequest, TelnyxConfigurationResponse
from .provider import TelnyxProvider
from .routes import router as routes_router
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "telnyx",
        "api_key": value.get("api_key"),
        "connection_id": value.get("connection_id"),
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
    name="telnyx",
    provider_cls=TelnyxProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    audio_config=_AUDIO_CONFIG,
    config_request_cls=TelnyxConfigurationRequest,
    router=routes_router,
    config_response_cls=TelnyxConfigurationResponse,
)


register(SPEC)


__all__ = [
    "SPEC",
    "TelnyxConfigurationRequest",
    "TelnyxConfigurationResponse",
    "TelnyxProvider",
    "create_transport",
]
