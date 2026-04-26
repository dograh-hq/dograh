"""Cloudonix telephony provider package."""

from typing import Any, Dict

from api.services.pipecat.audio_config import AudioConfig
from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import CloudonixConfigurationRequest, CloudonixConfigurationResponse
from .provider import CloudonixProvider
from .routes import router as routes_router
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


_UI_METADATA = ProviderUIMetadata(
    display_name="Cloudonix",
    docs_url="https://docs.cloudonix.io/",
    fields=[
        ProviderUIField(
            name="bearer_token",
            label="Bearer Token",
            type="password",
            sensitive=True,
            description="Cloudonix API Bearer Token",
        ),
        ProviderUIField(
            name="domain_id", label="Domain ID", type="text"
        ),
        ProviderUIField(
            name="from_numbers",
            label="Phone Numbers",
            type="string-array",
            required=False,
        ),
    ],
)


SPEC = ProviderSpec(
    name="cloudonix",
    provider_cls=CloudonixProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    audio_config=_AUDIO_CONFIG,
    config_request_cls=CloudonixConfigurationRequest,
    router=routes_router,
    ui_metadata=_UI_METADATA,
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
