"""ARI (Asterisk REST Interface) telephony provider package."""

from typing import Any, Dict

from api.services.pipecat.audio_config import AudioConfig
from api.services.telephony.registry import ProviderSpec, register

from .config import ARIConfigurationRequest, ARIConfigurationResponse
from .provider import ARIProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "ari",
        "ari_endpoint": value.get("ari_endpoint"),
        "app_name": value.get("app_name"),
        "app_password": value.get("app_password"),
        "inbound_workflow_id": value.get("inbound_workflow_id"),
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
    name="ari",
    provider_cls=ARIProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    audio_config=_AUDIO_CONFIG,
    config_request_cls=ARIConfigurationRequest,
    config_response_cls=ARIConfigurationResponse,
)


register(SPEC)


__all__ = [
    "SPEC",
    "ARIConfigurationRequest",
    "ARIConfigurationResponse",
    "ARIProvider",
    "create_transport",
]
