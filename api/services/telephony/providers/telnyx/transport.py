"""Telnyx transport factory."""

from fastapi import WebSocket

from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
from api.services.telephony.factory import load_credentials_for_transport
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from .serializers import TelnyxFrameSerializer


async def create_transport(
    websocket: WebSocket,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    *,
    vad_config: dict | None = None,
    ambient_noise_config: dict | None = None,
    telephony_configuration_id: int | None = None,
    stream_id: str,
    call_control_id: str,
):
    """Create a transport for Telnyx connections."""
    config = await load_credentials_for_transport(
        organization_id, telephony_configuration_id, expected_provider="telnyx"
    )

    api_key = config.get("api_key")
    if not api_key:
        raise ValueError(
            f"Incomplete Telnyx configuration for organization {organization_id}"
        )

    serializer = TelnyxFrameSerializer(
        stream_id=stream_id,
        call_control_id=call_control_id,
        api_key=api_key,
        outbound_encoding="PCMU",
        inbound_encoding="PCMU",
    )

    mixer = await build_audio_out_mixer(
        audio_config.transport_out_sample_rate, ambient_noise_config
    )

    return FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=audio_config.transport_in_sample_rate,
            audio_out_sample_rate=audio_config.transport_out_sample_rate,
            audio_out_mixer=mixer,
            serializer=serializer,
        ),
    )
