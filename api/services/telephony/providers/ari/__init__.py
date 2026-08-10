"""ARI (Asterisk REST Interface) telephony provider package."""

from typing import Any, Dict

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUICondition,
    ProviderUIField,
    ProviderUIMetadata,
    ProviderUIOption,
    register,
)

from .config import ARIConfigurationRequest, ARIConfigurationResponse
from .provider import ARIProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "ari",
        "ari_endpoint": value.get("ari_endpoint"),
        "app_name": value.get("app_name"),
        "app_password": value.get("app_password"),
        "external_pbx": value.get("external_pbx"),
        "from_numbers": value.get("from_numbers", []),
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="Asterisk ARI",
    docs_url="https://docs.dograh.com/integrations/telephony/asterisk-ari",
    fields=[
        ProviderUIField(
            name="ari_endpoint",
            label="ARI Endpoint",
            type="text",
            description="ARI base URL (e.g., http://asterisk.example.com:8088)",
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
            name="from_numbers",
            label="From Extensions",
            type="string-array",
            description="SIP extensions/numbers for outbound calls",
        ),
        ProviderUIField(
            name="external_pbx.type",
            label="External PBX Type",
            type="select",
            required=False,
            description=(
                "Enable PBX-specific call control for calls patched into Dograh "
                "through this Asterisk configuration."
            ),
            options=[ProviderUIOption(value="vicidial", label="VICIdial")],
            section="External PBX",
            feature_gate="external_pbx_integrations",
        ),
        ProviderUIField(
            name="external_pbx.agent_api.url",
            label="Agent API URL",
            type="text",
            description="Full VICIdial remote-agent API URL, ending in agc/api.php",
            placeholder="https://vici.example.com/agc/api.php",
            visible_when=ProviderUICondition(
                field="external_pbx.type", equals="vicidial"
            ),
            section="External PBX",
            feature_gate="external_pbx_integrations",
        ),
        ProviderUIField(
            name="external_pbx.agent_api.username",
            label="Agent API User",
            type="text",
            sensitive=True,
            visible_when=ProviderUICondition(
                field="external_pbx.type", equals="vicidial"
            ),
            section="External PBX",
            feature_gate="external_pbx_integrations",
        ),
        ProviderUIField(
            name="external_pbx.agent_api.password",
            label="Agent API Password",
            type="password",
            sensitive=True,
            visible_when=ProviderUICondition(
                field="external_pbx.type", equals="vicidial"
            ),
            section="External PBX",
            feature_gate="external_pbx_integrations",
        ),
        ProviderUIField(
            name="external_pbx.non_agent_api.url",
            label="Non-Agent API URL",
            type="text",
            required=False,
            description=(
                "Optional. Required only when a workflow updates VICIdial lead fields."
            ),
            placeholder="https://vici.example.com/vicidial/non_agent_api.php",
            visible_when=ProviderUICondition(
                field="external_pbx.type", equals="vicidial"
            ),
            section="External PBX",
            feature_gate="external_pbx_integrations",
        ),
        ProviderUIField(
            name="external_pbx.non_agent_api.username",
            label="Non-Agent API User",
            type="text",
            required=False,
            sensitive=True,
            visible_when=ProviderUICondition(
                field="external_pbx.type", equals="vicidial"
            ),
            section="External PBX",
            feature_gate="external_pbx_integrations",
        ),
        ProviderUIField(
            name="external_pbx.non_agent_api.password",
            label="Non-Agent API Password",
            type="password",
            required=False,
            sensitive=True,
            visible_when=ProviderUICondition(
                field="external_pbx.type", equals="vicidial"
            ),
            section="External PBX",
            feature_gate="external_pbx_integrations",
        ),
    ],
)


SPEC = ProviderSpec(
    name="ari",
    provider_cls=ARIProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=ARIConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=ARIConfigurationResponse,
)


register(SPEC)


__all__ = [
    "SPEC",
    "ARIConfigurationRequest",
    "ARIConfigurationResponse",
    "ARIProvider",
    "create_transport",
]
