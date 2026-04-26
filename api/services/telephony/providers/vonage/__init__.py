"""Vonage telephony provider package."""

from typing import Any, Dict

from api.services.pipecat.audio_config import AudioConfig
from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import VonageConfigurationRequest, VonageConfigurationResponse
from .provider import VonageProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "vonage",
        "application_id": value.get("application_id"),
        "private_key": value.get("private_key"),
        "api_key": value.get("api_key"),
        "api_secret": value.get("api_secret"),
        "from_numbers": value.get("from_numbers", []),
    }


_AUDIO_CONFIG = AudioConfig(
    transport_in_sample_rate=16000,
    transport_out_sample_rate=16000,
    vad_sample_rate=16000,
    pipeline_sample_rate=16000,
    buffer_size_seconds=5.0,
)


_UI_METADATA = ProviderUIMetadata(
    display_name="Vonage",
    docs_url="https://developer.vonage.com/en/voice/voice-api/overview",
    fields=[
        ProviderUIField(
            name="application_id", label="Application ID", type="text"
        ),
        ProviderUIField(
            name="private_key",
            label="Private Key",
            type="textarea",
            sensitive=True,
            description="Vonage RSA private key for JWT generation",
        ),
        ProviderUIField(
            name="api_key",
            label="API Key",
            type="text",
            sensitive=True,
            required=False,
        ),
        ProviderUIField(
            name="api_secret",
            label="API Secret",
            type="password",
            sensitive=True,
            required=False,
        ),
        ProviderUIField(
            name="from_numbers",
            label="Phone Numbers",
            type="string-array",
            description="Vonage phone numbers without + prefix",
        ),
    ],
)


SPEC = ProviderSpec(
    name="vonage",
    provider_cls=VonageProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    audio_config=_AUDIO_CONFIG,
    config_request_cls=VonageConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=VonageConfigurationResponse,
)


register(SPEC)


__all__ = [
    "SPEC",
    "VonageConfigurationRequest",
    "VonageConfigurationResponse",
    "VonageProvider",
    "create_transport",
]
