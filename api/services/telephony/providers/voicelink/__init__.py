"""VoiceLink telephony provider package."""

from typing import Any, Dict

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import VoiceLinkConfigurationRequest, VoiceLinkConfigurationResponse
from .provider import VoiceLinkProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "voicelink",
        "api_base": value.get("api_base"),
        "username": value.get("username"),
        "password": value.get("password"),
        "bearer_token": value.get("bearer_token"),
        "did_number": value.get("did_number"),
        "from_numbers": value.get("from_numbers", []),
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="VoiceLink",
    docs_url="https://docs.dograh.com/integrations/telephony/voicelink",
    fields=[
        ProviderUIField(
            name="api_base",
            label="API Base URL",
            type="text",
            required=False,
            description="VoiceLink API base URL",
            placeholder="https://app.voicelink.co.in/api",
        ),
        ProviderUIField(
            name="username",
            label="Username",
            type="text",
            required=False,
            sensitive=True,
            description=(
                "VoiceLink account username. Provide username + password so "
                "expired tokens can be refreshed automatically."
            ),
        ),
        ProviderUIField(
            name="password",
            label="Password",
            type="password",
            required=False,
            sensitive=True,
        ),
        ProviderUIField(
            name="bearer_token",
            label="Bearer Token",
            type="password",
            required=False,
            sensitive=True,
            description=(
                "Static VoiceLink bearer token. Optional when username and "
                "password are provided."
            ),
        ),
        ProviderUIField(
            name="did_number",
            label="DID Number",
            type="text",
            description=(
                "DID registered with VoiceLink in its registered form "
                "(e.g. 919484959244). Used as the caller id for outbound calls."
            ),
        ),
        ProviderUIField(
            name="from_numbers",
            label="Phone Numbers",
            type="string-array",
            description="VoiceLink DID numbers in registered form",
        ),
        ProviderUIField(
            name="client_id",
            label="Client ID",
            type="text",
            required=False,
            description=(
                "VoiceLink client id for this account. Optional — the KYC "
                "section uses it to scope KYC to this client; when unset, "
                "KYC acts on the reseller's own account."
            ),
        ),
    ],
)


SPEC = ProviderSpec(
    name="voicelink",
    provider_cls=VoiceLinkProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=VoiceLinkConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=VoiceLinkConfigurationResponse,
    account_id_credential_field="username",
)


register(SPEC)


__all__ = [
    "SPEC",
    "VoiceLinkConfigurationRequest",
    "VoiceLinkConfigurationResponse",
    "VoiceLinkProvider",
    "create_transport",
]
