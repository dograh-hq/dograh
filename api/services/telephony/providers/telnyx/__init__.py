"""Telnyx telephony provider package."""

from typing import Any, Dict

from api.services.pipecat.audio_config import AudioConfig
from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import TelnyxConfigurationRequest, TelnyxConfigurationResponse
from .provider import TelnyxProvider
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


_UI_METADATA = ProviderUIMetadata(
    display_name="Telnyx",
    docs_url="https://developers.telnyx.com/docs/voice",
    fields=[
        ProviderUIField(
            name="api_key", label="API Key", type="password", sensitive=True
        ),
        ProviderUIField(
            name="connection_id",
            label="Call Control App ID",
            type="text",
            description="Telnyx Call Control Application ID (connection_id)",
        ),
        ProviderUIField(
            name="from_numbers",
            label="Phone Numbers",
            type="string-array",
            description="E.164-formatted Telnyx phone numbers",
        ),
    ],
)


SPEC = ProviderSpec(
    name="telnyx",
    provider_cls=TelnyxProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    audio_config=_AUDIO_CONFIG,
    config_request_cls=TelnyxConfigurationRequest,
    ui_metadata=_UI_METADATA,
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
