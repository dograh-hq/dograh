"""Papi Voip telephony provider package."""

from typing import Any, Dict

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)
from .config import PapiVoipConfigurationRequest, PapiVoipConfigurationResponse
from .provider import PapiVoipProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "papi_voip",
        "api_key": value.get("api_key"),
        "instance_id": value.get("instance_id"),
        "base_url": value.get("base_url"),
        "from_numbers": value.get("from_numbers", []),
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="Papi Voip",
    docs_url="https://docs.papi.api.br",
    fields=[
        ProviderUIField(
            name="api_key",
            label="Instance API Key",
            type="password",
            sensitive=True,
            description=(
                "The API key for this WhatsApp instance, not the PAPI Cloud admin token."
            ),
        ),
        ProviderUIField(
            name="instance_id",
            label="Instance ID",
            type="text",
            required=True,
            description="WhatsApp Instance ID enabled with Voice/Voip",
        ),
    ],
)

SPEC = ProviderSpec(
    name="papi_voip",
    provider_cls=PapiVoipProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=16000,
    config_request_cls=PapiVoipConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=PapiVoipConfigurationResponse,
    account_id_credential_field="instance_id",
)

register(SPEC)

__all__ = [
    "SPEC",
    "PapiVoipConfigurationRequest",
    "PapiVoipConfigurationResponse",
    "PapiVoipProvider",
    "create_transport",
]
