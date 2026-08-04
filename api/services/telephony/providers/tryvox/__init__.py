"""Native TryVox telephony provider package."""

from typing import Any

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import TryVoxConfigurationRequest, TryVoxConfigurationResponse
from .provider import TryVoxProvider
from .transport import create_transport


def _config_loader(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": "tryvox",
        "auth_id": value.get("auth_id"),
        "auth_token": value.get("auth_token"),
        "webhook_secret": value.get("webhook_secret"),
        "application_id": value.get("application_id"),
        "api_base_url": value.get("api_base_url") or "https://api.tryvox.io",
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="TryVox",
    docs_url="https://docs.tryvox.io",
    fields=[
        ProviderUIField(
            name="auth_id",
            label="Auth ID",
            type="text",
            sensitive=True,
            description="TryVox account Auth ID",
        ),
        ProviderUIField(
            name="auth_token",
            label="Auth Token",
            type="password",
            sensitive=True,
            description="TryVox account Auth Token",
        ),
        ProviderUIField(
            name="webhook_secret",
            label="Webhook Secret",
            type="password",
            sensitive=True,
            description="Per-account secret used to verify voice webhooks",
        ),
        ProviderUIField(
            name="application_id",
            label="Voice Application ID",
            type="text",
            required=False,
            description="Required only for automatic inbound number setup",
        ),
        ProviderUIField(
            name="api_base_url",
            label="API Base URL",
            type="text",
            required=False,
            description="Keep the default unless using a private TryVox deployment",
        ),
        ProviderUIField(
            name="from_numbers",
            label="Phone Numbers",
            type="string-array",
            description="Account-owned E.164 numbers used for outbound calls",
        ),
    ],
)


SPEC = ProviderSpec(
    name="tryvox",
    provider_cls=TryVoxProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=TryVoxConfigurationRequest,
    config_response_cls=TryVoxConfigurationResponse,
    ui_metadata=_UI_METADATA,
    account_id_credential_field="auth_id",
)

register(SPEC)

__all__ = [
    "SPEC",
    "TryVoxConfigurationRequest",
    "TryVoxConfigurationResponse",
    "TryVoxProvider",
    "create_transport",
]
