"""Twilio telephony provider package."""

from typing import Any, Dict

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import TwilioConfigurationRequest, TwilioConfigurationResponse
from .provider import TwilioProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "twilio",
        "account_sid": value.get("account_sid"),
        "auth_token": value.get("auth_token"),
        "from_numbers": value.get("from_numbers", []),
        "amd_enabled": value.get("amd_enabled", False),
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="Twilio",
    docs_url="https://docs.dograh.com/integrations/telephony/twilio",
    fields=[
        ProviderUIField(
            name="account_sid",
            label="Account SID",
            type="text",
            sensitive=True,
            description="Twilio Account SID (starts with AC)",
        ),
        ProviderUIField(
            name="auth_token",
            label="Auth Token",
            type="password",
            sensitive=True,
            description="Twilio Auth Token",
        ),
        ProviderUIField(
            name="from_numbers",
            label="Phone Numbers",
            type="string-array",
            description="E.164-formatted Twilio phone numbers used for outbound calls",
        ),
        ProviderUIField(
            name="amd_enabled",
            label="Answering Machine Detection",
            type="boolean",
            description=(
                "Detect whether outbound calls are answered by a person or "
                "machine. Twilio may bill AMD as an additional per-call feature."
            ),
        ),
    ],
)


SPEC = ProviderSpec(
    name="twilio",
    provider_cls=TwilioProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=TwilioConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=TwilioConfigurationResponse,
    account_id_credential_field="account_sid",
)


register(SPEC)


__all__ = [
    "SPEC",
    "TwilioConfigurationRequest",
    "TwilioConfigurationResponse",
    "TwilioProvider",
    "create_transport",
]
