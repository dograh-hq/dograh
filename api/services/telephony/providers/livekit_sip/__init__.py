"""LiveKit SIP provider registration."""

from api.services.telephony.providers.livekit_sip.config import (
    LiveKitSipConfigurationRequest,
    LiveKitSipConfigurationResponse,
)
from api.services.telephony.providers.livekit_sip.provider import LiveKitSipProvider
from api.services.telephony.registry import ProviderSpec, ProviderUIField, ProviderUIMetadata, register


async def _noop_transport(**kwargs):
    """Stub — LiveKit SIP uses dograh-livekit AgentServer, not Pipecat."""
    raise NotImplementedError("LiveKit SIP does not use Pipecat transport")


def _config_loader(raw_config: dict) -> dict:
    """Reshape stored credentials into constructor dict."""
    return {
        "sip_trunk_id": raw_config.get("sip_trunk_id", ""),
        "from_numbers": [],  # Populated by factory from telephony_phone_numbers
    }


SPEC = ProviderSpec(
    name="livekit_sip",
    provider_cls=LiveKitSipProvider,
    config_loader=_config_loader,
    transport_factory=_noop_transport,
    transport_sample_rate=16000,
    config_request_cls=LiveKitSipConfigurationRequest,
    config_response_cls=LiveKitSipConfigurationResponse,
    ui_metadata=ProviderUIMetadata(
        display_name="LiveKit SIP",
        fields=[
            ProviderUIField(
                name="sip_trunk_id",
                label="SIP Trunk ID",
                type="text",
                required=True,
                sensitive=False,
            ),
        ],
    ),
    account_id_credential_field="",
)
register(SPEC)
