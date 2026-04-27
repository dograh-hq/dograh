"""Vobiz telephony provider package."""

from typing import Any, Dict

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

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


_UI_METADATA = ProviderUIMetadata(
    display_name="Vobiz",
    docs_url="https://docs.dograh.com/integrations/telephony/vobiz",
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
    transport_sample_rate=8000,
    config_request_cls=VobizConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=VobizConfigurationResponse,
    account_id_credential_field="auth_id",
)


register(SPEC)


__all__ = [
    "SPEC",
    "VobizConfigurationRequest",
    "VobizConfigurationResponse",
    "VobizProvider",
    "create_transport",
]
