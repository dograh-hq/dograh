"""WhatsApp telephony provider package.

This package implements the WhatsApp Business Calling API integration
for Dograh, following the TelephonyProvider interface pattern.

The provider supports:
- User-Initiated Calls (UIC): WhatsApp users calling the business
- Business-Initiated Calls (BIC): Business calling WhatsApp users (with permissions)
- WebRTC media transport with OPUS codec
- Graph API + Webhook signaling
- Call permission management

Integration Points:
- api.services.telephony.registry: Provider registration
- api.services.telephony.base: TelephonyProvider interface
- api.services.pipecat: Audio configuration and transport
- api.routes.telephony: Webhook endpoint mounting

Limitations:
- Business-initiated calls require user permission
- Webhook configuration must be done manually in Meta Developer Console
- Geographic restrictions apply to business-initiated calls
"""

from typing import Any, Dict

from api.services.telephony.registry import (
    ConfigurationSetupState,
    ProviderSetupChecklist,
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    ProviderUIOption,
    SetupStep,
    register,
)

from .config import WhatsAppConfigurationRequest, WhatsAppConfigurationResponse
from .provider import WhatsAppProvider
from .transport import create_transport


def _whatsapp_setup_checklist(
    credentials: Dict[str, Any],
    state: ConfigurationSetupState,
) -> ProviderSetupChecklist:
    """Report that WhatsApp outbound calling is under development."""
    return ProviderSetupChecklist.from_steps(
        steps=[
            SetupStep(
                key="outbound_unsupported",
                title="Outbound calling under development",
                description="Outbound calling via WhatsApp is currently under development. Only inbound calling is supported.",
                complete=False,
                blocks_outbound=True,
            )
        ],
        docs_url="https://docs.dograh.com/integrations/telephony/whatsapp",
    )


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize raw stored credentials into provider constructor format.
    
    Args:
        value: Raw credentials dict from TelephonyConfigurationModel.credentials
        
    Returns:
        Normalized dict matching WhatsAppProvider.__init__() signature
        
    Note:
        This function must be pure (no I/O) as it runs on read paths.
    """
    return {
        "provider": "whatsapp",
        "access_token": value.get("access_token"),
        "phone_number_id": value.get("phone_number_id"),
        "webhook_verify_token": value.get("webhook_verify_token"),
        "app_secret": value.get("app_secret"),
        "business_initiated_calls_enabled": value.get("business_initiated_calls_enabled", False),
        "call_icon_visibility": value.get("call_icon_visibility", "enabled"),
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="WhatsApp",
    docs_url="https://docs.dograh.com/integrations/telephony/whatsapp",
    fields=[
        ProviderUIField(
            name="webhook_verify_token",
            label="Webhook Verify Token",
            type="text",
            sensitive=True,
            section="Webhook Configuration",
            description="Paste this into Meta alongside Callback URL, click Verify and Save, and subscribe to the 'calls' field."
        ),
        ProviderUIField(
            name="access_token",
            label="Access Token",
            type="password",
            sensitive=True,
            section="Meta API Credentials",
            description="WhatsApp Business API access token from Meta Developer Console"
        ),
        ProviderUIField(
            name="phone_number_id",
            label="Phone Number ID",
            type="text",
            section="Meta API Credentials",
            description="Your WhatsApp Business phone number ID (found under WhatsApp > API Setup)"
        ),
        ProviderUIField(
            name="app_secret",
            label="App Secret",
            type="password",
            sensitive=True,
            section="Meta API Credentials",
            description="App secret for webhook signature validation"
        ),
        ProviderUIField(
            name="business_initiated_calls_enabled",
            label="Enable Business-Initiated Calls",
            type="boolean",
            section="Call Settings",
            description="Allow your business to initiate calls to WhatsApp users"
        ),
        ProviderUIField(
            name="call_icon_visibility",
            label="Call Icon Visibility",
            type="select",
            options=[
                ProviderUIOption(value="enabled", label="Enabled"),
                ProviderUIOption(value="disabled", label="Disabled"),
                ProviderUIOption(value="business_hours", label="Business Hours Only")
            ],
            section="Call Settings",
            description="Control when the call icon appears to users"
        )
    ]
)


SPEC = ProviderSpec(
    name="whatsapp",
    provider_cls=WhatsAppProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=16000,
    config_request_cls=WhatsAppConfigurationRequest,
    config_response_cls=WhatsAppConfigurationResponse,
    ui_metadata=_UI_METADATA,
    account_id_credential_field="phone_number_id",  # Used for webhook routing
    # WhatsApp is a carrier you buy numbers from, not BYO-SIP
    connectivity="api",
    # Outbound calls require at least one phone number
    requires_caller_id=True,
    setup_checklist_resolver=_whatsapp_setup_checklist,
)


register(SPEC)


__all__ = [
    "SPEC",
    "WhatsAppConfigurationRequest",
    "WhatsAppConfigurationResponse",
    "WhatsAppProvider",
    "create_transport",
]
