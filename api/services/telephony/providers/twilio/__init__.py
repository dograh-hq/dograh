"""Twilio telephony provider package."""

from typing import Any, Dict

from api.services.pipecat.audio_config import AudioConfig
from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import TwilioConfigurationRequest, TwilioConfigurationResponse
from .provider import TwilioProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "twilio",
        "account_sid": value.get("account_sid"),
        "auth_token": value.get("auth_token"),
        "from_numbers": value.get("from_numbers", []),
    }


_AUDIO_CONFIG = AudioConfig(
    transport_in_sample_rate=8000,
    transport_out_sample_rate=8000,
    vad_sample_rate=8000,
    pipeline_sample_rate=8000,
    buffer_size_seconds=5.0,
)


_UI_METADATA = ProviderUIMetadata(
    display_name="Twilio",
    docs_url="https://www.twilio.com/docs/voice",
    fields=[
        ProviderUIField(
            name="account_sid",
            label="Account SID",
            type="text",
            sensitive=True,
            description="Twilio Account SID (starts with AC)",
        ),
        ProviderUIField(
            name="auth_token",
            label="Auth Token",
            type="password",
            sensitive=True,
            description="Twilio Auth Token",
        ),
        ProviderUIField(
            name="from_numbers",
            label="Phone Numbers",
            type="string-array",
            description="E.164-formatted Twilio phone numbers used for outbound calls",
        ),
    ],
)


SPEC = ProviderSpec(
    name="twilio",
    provider_cls=TwilioProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    audio_config=_AUDIO_CONFIG,
    config_request_cls=TwilioConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=TwilioConfigurationResponse,
    account_id_credential_field="account_sid",
)


register(SPEC)


__all__ = [
    "SPEC",
    "TwilioConfigurationRequest",
    "TwilioConfigurationResponse",
    "TwilioProvider",
    "create_transport",
]
