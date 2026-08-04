"""VoxPro (India VNO) telephony provider package.

Registering this package adds VoxPro to Dograh's telephony provider list.
VoxPro is a licensed Indian VNO offering managed PSTN (own 1400/1600 range) via
its connector API — Twilio-style simplicity, India-native, DPDP-compliant.
"""

from typing import Any, Dict

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import VoxProConfigurationRequest, VoxProConfigurationResponse
from .provider import VoxProProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "voxpro",
        "api_key": value.get("api_key"),
        "tenant_id": value.get("tenant_id"),
        "api_base": value.get("api_base"),
        "from_numbers": value.get("from_numbers", []),
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="VoxPro (India VNO)",
    docs_url="https://docs.dograh.com/integrations/telephony/voxpro",
    fields=[
        ProviderUIField(
            name="api_key",
            label="VoxPro API Key",
            type="password",
            sensitive=True,
            description="Per-tenant API key issued by VoxPro for the connector API",
        ),
        ProviderUIField(
            name="tenant_id",
            label="Tenant ID",
            type="text",
            description="VoxPro tenant / X-Tenant-ID (e.g., AI_Katha_1783948668)",
        ),
        ProviderUIField(
            name="api_base",
            label="Connector Base URL",
            type="text",
            required=False,
            description="VoxPro AI Connector base URL (leave default unless self-hosted)",
        ),
        ProviderUIField(
            name="from_numbers",
            label="Phone Numbers (DIDs)",
            type="string-array",
            description="VoxPro DIDs in E.164 without + prefix",
        ),
    ],
)


SPEC = ProviderSpec(
    name="voxpro",
    provider_cls=VoxProProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=VoxProConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=VoxProConfigurationResponse,
    account_id_credential_field="tenant_id",
)


register(SPEC)


__all__ = [
    "SPEC",
    "VoxProConfigurationRequest",
    "VoxProConfigurationResponse",
    "VoxProProvider",
    "create_transport",
]
