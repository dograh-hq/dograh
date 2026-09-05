"""Smartflo telephony provider package."""

from typing import Any, Dict

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import SmartfloConfigurationRequest
from .provider import SmartfloProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "smartflo",
        "click_to_call_api_key": value.get("click_to_call_api_key"),
        "smartflo_jwt_token": value.get("smartflo_jwt_token"),
        "smartflo_did_number": value.get("smartflo_did_number"),
        "smartflo_api_domain": value.get(
            "smartflo_api_domain", "https://api-smartflo.tatateleservices.com"
        ),
        "from_numbers": value.get("from_numbers", []),
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="Tata Smartflo",
    docs_url="https://www.tatateleservices.com/smartflo",
    fields=[
        ProviderUIField(
            name="click_to_call_api_key",
            label="Click-to-Call API Key",
            type="password",
            sensitive=True,
            description="Tata Smartflo Click-to-Call Support API Key",
        ),
        ProviderUIField(
            name="smartflo_jwt_token",
            label="JWT Bearer Token",
            type="password",
            sensitive=True,
            required=False,
            description="Smartflo API authorization token",
        ),
        ProviderUIField(
            name="smartflo_did_number",
            label="Caller ID / DID Number",
            type="text",
            required=False,
            description="Smartflo Virtual / DID phone number used for caller ID",
        ),
        ProviderUIField(
            name="smartflo_api_domain",
            label="API Domain",
            type="text",
            required=False,
            description="Smartflo API base domain (e.g. https://api-smartflo.tatateleservices.com)",
        ),
    ],
)


SPEC = ProviderSpec(
    name="smartflo",
    provider_cls=SmartfloProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=SmartfloConfigurationRequest,
    ui_metadata=_UI_METADATA,
    account_id_credential_field="click_to_call_api_key",
)


register(SPEC)


__all__ = [
    "SPEC",
    "SmartfloConfigurationRequest",
    "SmartfloProvider",
    "create_transport",
]
