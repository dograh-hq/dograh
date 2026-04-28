"""Cloudonix telephony provider package."""

from typing import Any, Dict

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import CloudonixConfigurationRequest, CloudonixConfigurationResponse
from .provider import CloudonixProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "cloudonix",
        "bearer_token": value.get("bearer_token"),
        "api_key": value.get("api_key"),  # For x-cx-apikey validation
        "domain_id": value.get("domain_id"),
        "application_name": value.get("application_name"),
        "from_numbers": value.get("from_numbers", []),
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="Cloudonix",
    docs_url="https://docs.dograh.com/integrations/telephony/cloudonix",
    fields=[
        ProviderUIField(
            name="bearer_token",
            label="Bearer Token",
            type="password",
            sensitive=True,
            description="Cloudonix API Bearer Token",
        ),
        ProviderUIField(name="domain_id", label="Domain ID", type="text"),
        ProviderUIField(
            name="application_name",
            label="Application Name",
            type="text",
            description=(
                "Cloudonix Voice Application name whose url is updated when "
                "inbound workflows are attached to numbers on this domain"
            ),
        ),
        ProviderUIField(
            name="from_numbers",
            label="Phone Numbers",
            type="string-array",
            required=False,
        ),
    ],
)


SPEC = ProviderSpec(
    name="cloudonix",
    provider_cls=CloudonixProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=CloudonixConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=CloudonixConfigurationResponse,
    account_id_credential_field="domain_id",
)


register(SPEC)


__all__ = [
    "SPEC",
    "CloudonixConfigurationRequest",
    "CloudonixConfigurationResponse",
    "CloudonixProvider",
    "create_transport",
]
