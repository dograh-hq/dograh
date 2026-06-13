"""Tone telephony provider package."""

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import ToneConfigurationRequest, ToneConfigurationResponse
from .provider import ToneProvider
from .transport import create_transport


def _config_loader(value: dict) -> dict:
    return {
        "provider": "tone",
        "api_key": value.get("api_key"),
        "from_numbers": value.get("from_numbers", []),
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="Tone",
    docs_url="https://docs.usetone.ai",
    fields=[
        ProviderUIField(
            name="api_key",
            label="API Key",
            type="password",
            sensitive=True,
            description="Your Tone API key from usetone.ai/dashboard/api-keys",
        ),
        ProviderUIField(
            name="from_numbers",
            label="Phone Numbers",
            type="string-array",
            description="E.164-formatted Tone phone numbers, e.g. +917314624707",
        ),
    ],
)


SPEC = ProviderSpec(
    name="tone",
    provider_cls=ToneProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=ToneConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=ToneConfigurationResponse,
    account_id_credential_field="api_key",
)


register(SPEC)


__all__ = [
    "SPEC",
    "ToneConfigurationRequest",
    "ToneConfigurationResponse",
    "ToneProvider",
    "create_transport",
]
