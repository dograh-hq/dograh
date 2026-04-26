"""Twilio telephony provider package."""

from typing import Any, Dict

from api.services.pipecat.audio_config import AudioConfig
from api.services.telephony.registry import ProviderSpec, register

from .config import TwilioConfigurationRequest, TwilioConfigurationResponse
from .provider import TwilioProvider
from .routes import router as routes_router
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


SPEC = ProviderSpec(
    name="twilio",
    provider_cls=TwilioProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    audio_config=_AUDIO_CONFIG,
    config_request_cls=TwilioConfigurationRequest,
    router=routes_router,
    config_response_cls=TwilioConfigurationResponse,
)


register(SPEC)


__all__ = [
    "SPEC",
    "TwilioConfigurationRequest",
    "TwilioConfigurationResponse",
    "TwilioProvider",
    "create_transport",
]
