"""Vobiz telephony provider package."""

from typing import Any, Dict

from api.services.pipecat.audio_config import AudioConfig
from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import VobizConfigurationRequest, VobizConfigurationResponse
from .provider import VobizProvider
from .routes import router as routes_router
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


_UI_METADATA = ProviderUIMetadata(
    display_name="Vobiz",
    fields=[
        ProviderUIField(
            name="auth_id",
            label="Account ID",
            type="text",
            sensitive=True,
            description="Vobiz Account ID (e.g., MA_SYQRLN1K)",
        ),
        ProviderUIField(
            name="auth_token", label="Auth Token", type="password", sensitive=True
        ),
        ProviderUIField(
            name="from_numbers",
            label="Phone Numbers",
            type="string-array",
            description="E.164-formatted phone numbers without + prefix",
        ),
    ],
)


SPEC = ProviderSpec(
    name="vobiz",
    provider_cls=VobizProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    audio_config=_AUDIO_CONFIG,
    config_request_cls=VobizConfigurationRequest,
    router=routes_router,
    ui_metadata=_UI_METADATA,
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
