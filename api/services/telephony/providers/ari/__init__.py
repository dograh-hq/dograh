"""ARI (Asterisk REST Interface) telephony provider package."""

import secrets
from typing import Any, Dict

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUICondition,
    ProviderUIField,
    ProviderUIMetadata,
    ProviderUIOption,
    register,
)

from .config import ARIConfigurationRequest
from .provider import ARIProvider
from .transport import create_transport

# Prefix keeps the generated name recognisable in a customer's dialplan; the
# random half is what makes it unique.
_STASIS_APP_NAME_PREFIX = "dograh_"


def _generate_stasis_app_name() -> str:
    """Mint a Stasis application name no other configuration will pick.

    Asterisk hands a Stasis application to whichever ARI WebSocket registered
    for it most recently and silently stops delivering events to the previous
    holder. Two configurations naming the same application on the same PBX
    therefore do not both work — one goes deaf, with no error on either side,
    and the survivor receives calls belonging to the other.

    Uniqueness cannot be validated into a customer-chosen string: the colliding
    party may be another tenant, a self-hosted install, or another deployment
    entirely, and nothing in our database sees them. So we do not ask. The name
    is generated here and the customer mirrors it into their extensions.conf.
    """
    return f"{_STASIS_APP_NAME_PREFIX}{secrets.token_hex(6)}"


async def _preprocess_credentials_on_save(
    credentials: Dict[str, Any],
    existing_credentials: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Own the Stasis application name: minted on create, carried on update.

    The value is always written from here and never read from the request, so a
    client cannot choose or overwrite it. Saving replaces the stored credentials
    wholesale, which is why an update has to restate it.

    An update never mints one. A configuration saved before the Stasis name was
    split off ``app_name`` keeps running on ``app_name`` (see
    ``_config_loader``); generating one here would point Dograh at an
    application the customer's dialplan never routes calls into, silently
    breaking a working setup on an unrelated edit.
    """
    credentials = dict(credentials)
    if existing_credentials is None:
        credentials["stasis_app_name"] = _generate_stasis_app_name()
    elif existing_credentials.get("stasis_app_name"):
        credentials["stasis_app_name"] = existing_credentials["stasis_app_name"]
    else:
        credentials.pop("stasis_app_name", None)
    return credentials


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "ari",
        "ari_endpoint": value.get("ari_endpoint"),
        "app_name": value.get("app_name"),
        "app_password": value.get("app_password"),
        # Pre-split configurations have no stasis_app_name and still run on
        # app_name, which is what they told Asterisk to use.
        "stasis_app_name": value.get("stasis_app_name") or value.get("app_name"),
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
            label="ARI Username",
            type="text",
            description="ARI username, matching the section name in ari.conf",
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
            name="stasis_app_name",
            label="Stasis App Name",
            type="readonly",
            required=False,
            description=(
                "Generated for you, and unique to this configuration. Route "
                "calls into it from extensions.conf with Stasis(<this name>)."
            ),
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
    # The customer's own Asterisk carries the calls; Dograh only rides it.
    connectivity="sip",
    # Origination sets ``callerId`` only when one is configured — a PBX
    # dialling an internal extension needs no number at all.
    requires_caller_id=False,
    # Assigns stasis_app_name. Deliberately not listed in
    # server_managed_credential_fields: those are dropped from the
    # configuration response, and this one has to stay readable so the customer
    # can paste it into their own extensions.conf.
    preprocess_credentials_on_save=_preprocess_credentials_on_save,
)


register(SPEC)


__all__ = [
    "SPEC",
    "ARIConfigurationRequest",
    "ARIProvider",
    "create_transport",
]
