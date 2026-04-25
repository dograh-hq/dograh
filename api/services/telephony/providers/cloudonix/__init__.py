"""Cloudonix telephony provider package."""

from typing import Any, Dict

from api.services.pipecat.audio_config import AudioConfig
from api.services.telephony.registry import ProviderSpec, register

from .config import CloudonixConfigurationRequest, CloudonixConfigurationResponse
from .provider import CloudonixProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "cloudonix",
        "bearer_token": value.get("bearer_token"),
        "api_key": value.get("api_key"),  # For x-cx-apikey validation
        "domain_id": value.get("domain_id"),
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
    name="cloudonix",
    provider_cls=CloudonixProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    audio_config=_AUDIO_CONFIG,
    config_request_cls=CloudonixConfigurationRequest,
    config_response_cls=CloudonixConfigurationResponse,
)


register(SPEC)


__all__ = [
    "SPEC",
    "CloudonixConfigurationRequest",
    "CloudonixConfigurationResponse",
    "CloudonixProvider",
    "create_transport",
]
