"""Exotel telephony provider package.

Importing this module registers ExotelProvider with the telephony registry.
"""

from typing import Any, Dict

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import ExotelConfigurationRequest, ExotelConfigurationResponse
from .provider import ExotelProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize stored credentials JSONB into the ExotelProvider constructor dict."""
    return {
        "provider": "exotel",
        "api_key": value.get("api_key"),
        "api_token": value.get("api_token"),
        "account_sid": value.get("account_sid"),
        "subdomain": value.get("subdomain", "api.exotel.com"),
        "from_numbers": value.get("from_numbers", []),
        "app_id": value.get("app_id"),
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="Exotel",
    docs_url="https://developer.exotel.com/api/",
    fields=[
        ProviderUIField(
            name="api_key",
            label="API Key",
            type="text",
            sensitive=True,
            description="From Exotel Dashboard → Settings → API Settings",
        ),
        ProviderUIField(
            name="api_token",
            label="API Token",
            type="password",
            sensitive=True,
        ),
        ProviderUIField(
            name="account_sid",
            label="Account SID",
            type="text",
            sensitive=False,
            description="Your Exotel account SID / subdomain identifier",
        ),
        ProviderUIField(
            name="subdomain",
            label="API Subdomain",
            type="text",
            sensitive=False,
            required=False,
            description=(
                "api.exotel.com (global / SEA) or "
                "api.in.exotel.com (India-hosted). Defaults to api.exotel.com."
            ),
        ),
        ProviderUIField(
            name="from_numbers",
            label="ExoPhone Numbers (CallerIds)",
            type="string-array",
            description=(
                "Exotel virtual phone numbers used as CallerIds for outbound calls. "
                "Add them without country code if your account expects that format."
            ),
        ),
        ProviderUIField(
            name="app_id",
            label="App ID (optional)",
            type="text",
            sensitive=False,
            required=False,
            description=(
                "Exotel App Bazaar flow ID for inbound call routing. "
                "Leave blank if you are configuring the answer URL manually."
            ),
        ),
    ],
)

SPEC = ProviderSpec(
    name="exotel",
    provider_cls=ExotelProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,  # μ-law 8 kHz
    config_request_cls=ExotelConfigurationRequest,
    config_response_cls=ExotelConfigurationResponse,
    ui_metadata=_UI_METADATA,
    # AccountSid is present on all Exotel inbound webhooks.
    account_id_credential_field="account_sid",
)

register(SPEC)

__all__ = [
    "SPEC",
    "ExotelConfigurationRequest",
    "ExotelConfigurationResponse",
    "ExotelProvider",
    "create_transport",
]
