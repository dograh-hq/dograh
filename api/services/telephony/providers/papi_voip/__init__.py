"""Papi Voip telephony provider package."""

from typing import Any, Dict

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)
from .config import PapiVoipConfigurationRequest
from .provider import PapiVoipProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "papi_voip",
        "api_key": value.get("api_key"),
        "instance_id": value.get("instance_id"),
        "base_url": value.get("base_url") or "https://api.papi.api.br",
        "webhook_secret": value.get("webhook_secret"),
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="Papi Voip (WhatsApp Voice)",
    docs_url="https://papi.api.br/docs",
    fields=[
        ProviderUIField(
            name="base_url",
            label="API Base URL",
            type="text",
            placeholder="https://api.papi.api.br",
            description="PAPI Cloud API Base URL (HTTPS)",
        ),
        ProviderUIField(
            name="api_key",
            label="API Key / Token",
            type="password",
            sensitive=True,
            description="API key for WhatsApp VoIP instance",
        ),
        ProviderUIField(
            name="instance_id",
            label="Instance ID",
            type="text",
            description="WhatsApp instance ID with Voice/SIP enabled",
        ),
        ProviderUIField(
            name="webhook_secret",
            label="Webhook Secret (Optional)",
            type="password",
            sensitive=True,
            description="HMAC secret key for callback signature verification",
        ),
    ],
)

PAPI_VOIP_SPEC = ProviderSpec(
    name="papi_voip",
    provider_cls=PapiVoipProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=16000,
    config_request_cls=PapiVoipConfigurationRequest,
    ui_metadata=_UI_METADATA,
    connectivity="api",
)

register(PAPI_VOIP_SPEC)
