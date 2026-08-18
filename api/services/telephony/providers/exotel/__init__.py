"""Exotel telephony provider package."""

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
    # Do not load from_numbers from credentials — factory attaches active
    # phone numbers from telephony_phone_numbers after this runs.
    return {
        "provider": "exotel",
        "account_sid": value.get("account_sid"),
        "api_key": value.get("api_key"),
        "api_token": value.get("api_token"),
        "api_base_url": value.get("api_base_url") or "https://api.in.exotel.com",
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="Exotel",
    docs_url="https://docs.dograh.com/integrations/telephony/exotel",
    fields=[
        ProviderUIField(
            name="account_sid",
            label="Account SID",
            type="text",
            sensitive=True,
            description="Exotel Account SID used in API paths and inbound matching",
        ),
        ProviderUIField(
            name="api_key",
            label="API Key",
            type="password",
            sensitive=True,
        ),
        ProviderUIField(
            name="api_token",
            label="API Token",
            type="password",
            sensitive=True,
        ),
        ProviderUIField(
            name="api_base_url",
            label="API Base URL",
            type="text",
            required=False,
            description=(
                "Defaults to https://api.in.exotel.com. "
                "Use https://api.exotel.com for non-India accounts."
            ),
        ),
    ],
)


SPEC = ProviderSpec(
    name="exotel",
    provider_cls=ExotelProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=ExotelConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=ExotelConfigurationResponse,
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
