"""VoiceLink telephony provider package."""

from typing import Any, Dict

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    ProviderUIOption,
    register,
)

from .config import (
    PRODUCTION_API_BASE_URL,
    UAT_API_BASE_URL,
    VoiceLinkConfigurationRequest,
)
from .provider import VoiceLinkProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    # Do not load from_numbers from credentials — factory attaches active
    # phone numbers from telephony_phone_numbers after this runs.
    return {
        "provider": "voicelink",
        "api_token": value.get("api_token"),
        "client_id": value.get("client_id"),
        "api_base_url": value.get("api_base_url") or PRODUCTION_API_BASE_URL,
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="VoiceLink",
    fields=[
        ProviderUIField(
            name="api_token",
            label="API Token",
            type="password",
            sensitive=True,
            description="VoiceLink API token",
        ),
        ProviderUIField(
            name="client_id",
            label="Client ID",
            type="number",
            description=(
                "Your VoiceLink client id. Inbound calls are matched to this "
                "configuration by it."
            ),
        ),
        ProviderUIField(
            name="api_base_url",
            label="Environment",
            type="select",
            required=False,
            description="Defaults to production.",
            options=[
                ProviderUIOption(value=PRODUCTION_API_BASE_URL, label="Production"),
                ProviderUIOption(value=UAT_API_BASE_URL, label="UAT"),
            ],
        ),
    ],
)


SPEC = ProviderSpec(
    name="voicelink",
    provider_cls=VoiceLinkProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    # VoiceLink streams A-law at 8 kHz on every observed call, whatever the
    # bot's configured audio format.
    transport_sample_rate=8000,
    config_request_cls=VoiceLinkConfigurationRequest,
    ui_metadata=_UI_METADATA,
    account_id_credential_field="client_id",
)

register(SPEC)

__all__ = [
    "SPEC",
    "VoiceLinkConfigurationRequest",
    "VoiceLinkProvider",
    "create_transport",
]
