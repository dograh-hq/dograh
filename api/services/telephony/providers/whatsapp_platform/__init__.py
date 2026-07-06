"""WhatsApp Platform telephony provider package.

This package lets Dograh receive inbound WhatsApp calls from the
WhatsApp SaaS platform bridge (Node.js backend + @roamhq/wrtc).

The bridge side already handles Meta WebRTC negotiation and PCM
resampling. By the time audio reaches Dograh, it is a standard
16kHz mono Linear PCM WebSocket stream — exactly what Dograh's
pipecat pipeline expects.

Inbound-only. Outbound calls are not supported because WhatsApp
Business Policy requires explicit opt-in permission from the
recipient before placing a call.
"""

from typing import Any, Dict

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import (
    WhatsAppPlatformConfigurationRequest,
    WhatsAppPlatformConfigurationResponse,
)
from .provider import WhatsAppPlatformProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider":            "whatsapp_platform",
        "platform_api_url":    value.get("platform_api_url"),
        "webhook_secret":      value.get("webhook_secret"),
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="WhatsApp Platform",
    docs_url="https://docs.dograh.com/integrations/telephony/whatsapp-platform",
    fields=[
        ProviderUIField(
            name="platform_api_url",
            label="Platform API URL",
            type="text",
            placeholder="https://your-whatsapp-saas-backend.com",
            required=True,
            description=(
                "Base URL of your WhatsApp SaaS backend. "
                "Dograh will call /dograh-webhook/* on this host."
            ),
        ),
        ProviderUIField(
            name="webhook_secret",
            label="Webhook Secret",
            type="password",
            placeholder="A strong shared secret (32+ chars)",
            required=True,
            description=(
                "Shared secret used to authenticate Dograh → Platform webhooks. "
                "Must match the key configured in the Platform admin panel."
            ),
        ),
    ],
)

_PROVIDER_SPEC = ProviderSpec(
    name="whatsapp_platform",
    provider_cls=WhatsAppPlatformProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=16000,
    config_request_cls=WhatsAppPlatformConfigurationRequest,
    config_response_cls=WhatsAppPlatformConfigurationResponse,
    ui_metadata=_UI_METADATA,
)

register(_PROVIDER_SPEC)
