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

from .config import (
    DEFAULT_WHATSAPP_PERMISSION_MESSAGE,
    WhatsAppConfigurationRequest,
    WhatsAppConfigurationResponse,
)
from .provider import WhatsAppProvider
from .service import resolve_live_call_state
from .transport import create_transport


def _whatsapp_setup_checklist(
    credentials: Dict[str, Any],
    state: ConfigurationSetupState,
) -> ProviderSetupChecklist:
    """Report readiness for WhatsApp outbound calling."""
    has_phone_number = state.active_phone_number_count > 0
    bic_enabled = bool(credentials.get("business_initiated_calls_enabled", False))

    steps = [
        SetupStep(
            key="caller_id",
            title="Add a phone number to use as caller ID",
            description=(
                "Outbound calls need a number to dial from. Add at least one "
                "active WhatsApp phone number under Phone numbers below."
            ),
            complete=has_phone_number,
            blocks_outbound=True,
        ),
        SetupStep(
            key="business_initiated_calls",
            title="Enable Business-Initiated Calls",
            description=(
                "Enable 'Business-Initiated Calls' in the configuration settings "
                "to place outbound calls via WhatsApp."
            ),
            complete=bic_enabled,
            blocks_outbound=True,
        ),
    ]
    return ProviderSetupChecklist.from_steps(
        steps=steps,
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
        "default_permission_message": value.get("default_permission_message"),
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
        ),
        ProviderUIField(
            name="default_permission_message",
            label="Call Permission Request Message",
            type="textarea",
            required=False,
            section="Call Settings",
            placeholder=DEFAULT_WHATSAPP_PERMISSION_MESSAGE,
            description=(
                "The message shown to recipients when they receive a call permission request from your business. "
                "If left blank, the default message will be used."
            ),
        )

    ]
)


SPEC = ProviderSpec(
    name="whatsapp",
    provider_cls=WhatsAppProvider,
    config_loader=_config_loader,
    # Both fields are required by ProviderSpec but inert for WhatsApp: media is
    # WebRTC, so calls run through run_pipeline_smallwebrtc rather than
    # run_pipeline_telephony, which is the only caller of transport_factory and
    # the only consumer of transport_sample_rate. 16000 matches what the live
    # SmallWebRTC path actually uses (create_audio_config caps the pipeline at
    # 16 kHz for VAD; aiortc resamples Meta's 48 kHz Opus down to it in
    # SmallWebRTCTransport). See transport.py for the full trace -- the factory
    # raises rather than building a transport Meta will never speak to.
    transport_factory=create_transport,
    transport_sample_rate=16000,
    config_request_cls=WhatsAppConfigurationRequest,
    config_response_cls=WhatsAppConfigurationResponse,
    ui_metadata=_UI_METADATA,
    requires_call_permission=True,
    account_id_credential_field="phone_number_id",  # Used for webhook routing
    # Lets the shared call-status route read live WhatsApp call state without
    # importing this package, and without the DB round-trip a provider
    # instantiation would cost on a once-a-second poll.
    live_call_state_resolver=resolve_live_call_state,
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
