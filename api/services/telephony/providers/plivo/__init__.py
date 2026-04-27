"""Plivo telephony provider package."""

from typing import Any, Dict

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import PlivoConfigurationRequest, PlivoConfigurationResponse
from .provider import PlivoProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "plivo",
        "auth_id": value.get("auth_id"),
        "auth_token": value.get("auth_token"),
        "application_id": value.get("application_id"),
        "from_numbers": value.get("from_numbers", []),
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="Plivo",
    docs_url="https://docs.dograh.com/integrations/telephony/plivo",
    fields=[
        ProviderUIField(name="auth_id", label="Auth ID", type="text", sensitive=True),
        ProviderUIField(
            name="auth_token", label="Auth Token", type="password", sensitive=True
        ),
        ProviderUIField(
            name="application_id",
            label="Application ID",
            type="text",
            description=(
                "Plivo Application ID whose answer_url is updated when inbound "
                "workflows are attached to numbers on this account"
            ),
        ),
        ProviderUIField(
            name="from_numbers",
            label="Phone Numbers",
            type="string-array",
            description="E.164-formatted Plivo phone numbers used for outbound calls",
        ),
    ],
)


SPEC = ProviderSpec(
    name="plivo",
    provider_cls=PlivoProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=PlivoConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=PlivoConfigurationResponse,
    account_id_credential_field="auth_id",
)


register(SPEC)


__all__ = [
    "SPEC",
    "PlivoConfigurationRequest",
    "PlivoConfigurationResponse",
    "PlivoProvider",
    "create_transport",
]
