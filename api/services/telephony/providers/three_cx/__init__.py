"""3CX (PJSIP trunk via Asterisk bridge) telephony provider package."""

from typing import Any, Dict

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import ThreeCxConfigurationRequest, ThreeCxConfigurationResponse
from .provider import ThreeCxProvider
from .provisioning import _provision_3cx_trunk, endpoint_id_for
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    """Reshape stored JSONB credentials into the provider constructor dict."""
    return {
        "provider": "three_cx",
        "ari_endpoint": value.get("ari_endpoint"),
        "app_name": value.get("app_name"),
        "app_password": value.get("app_password"),
        "ws_client_name": value.get("ws_client_name", ""),
        "sip_domain": value.get("sip_domain"),
        "extension": value.get("extension"),
        "strip_prefix": value.get("strip_prefix", ""),
        "from_numbers": value.get("from_numbers", []),
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="3CX (Asterisk bridge)",
    docs_url="https://docs.dograh.com/integrations/telephony/three-cx",
    fields=[
        ProviderUIField(
            name="ari_endpoint",
            label="ARI Endpoint",
            type="text",
            description="ARI base URL of the bridging Asterisk (http://host:8088)",
        ),
        ProviderUIField(
            name="app_name",
            label="Stasis App Name",
            type="text",
            description="Stasis application name registered in Asterisk",
        ),
        ProviderUIField(
            name="app_password",
            label="ARI Password",
            type="password",
            sensitive=True,
        ),
        ProviderUIField(
            name="ws_client_name",
            label="websocket_client.conf Name",
            type="text",
            description="websocket_client.conf connection name for externalMedia",
        ),
        ProviderUIField(
            name="sip_domain",
            label="3CX SIP Domain",
            type="text",
            description="Your 3CX cloud host (e.g. 1156.3cx.cloud)",
            placeholder="1156.3cx.cloud",
        ),
        ProviderUIField(
            name="extension",
            label="3CX Extension",
            type="text",
            description="Extension number registered for Dograh (e.g. 12611)",
            placeholder="12611",
        ),
        ProviderUIField(
            name="sip_password",
            label="SIP Password",
            type="password",
            sensitive=True,
            description="SIP auth password for the extension on 3CX",
        ),
        ProviderUIField(
            name="strip_prefix",
            label="Strip Prefix (regex)",
            type="text",
            required=False,
            description=(
                "Optional regex stripped from outbound numbers before dialling. "
                "Only the literal '^\\+<digits>' form is supported "
                "(Italian deployments use '^\\+39')."
            ),
            placeholder="^\\+39",
        ),
        ProviderUIField(
            name="from_numbers",
            label="From Numbers",
            type="string-array",
            required=False,
            description="E.164 caller-IDs permitted on outbound calls",
        ),
    ],
)


SPEC = ProviderSpec(
    name="three_cx",
    provider_cls=ThreeCxProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=ThreeCxConfigurationRequest,
    config_response_cls=ThreeCxConfigurationResponse,
    ui_metadata=_UI_METADATA,
    account_id_credential_field="extension",
    preprocess_credentials_on_save=_provision_3cx_trunk,
)


register(SPEC)


__all__ = [
    "SPEC",
    "ThreeCxConfigurationRequest",
    "ThreeCxConfigurationResponse",
    "ThreeCxProvider",
    "create_transport",
    "endpoint_id_for",
]
