"""Telnyx transport factory."""

from fastapi import WebSocket
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
from api.services.telephony.factory import load_credentials_for_transport

from .serializers import TelnyxFrameSerializer
from .strategies import TelnyxConferenceStrategy, TelnyxHangupStrategy


async def create_transport(
    websocket: WebSocket,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    *,
    ambient_noise_config: dict | None = None,
    telephony_configuration_id: int | None = None,
    stream_id: str,
    call_control_id: str,
    encoding: str = "PCMU",
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

    # Pipecat's TelnyxFrameSerializer names its params from the call's POV,
    # not Dograh's: ``inbound_encoding`` is what we *send into the call*
    # (Dograh → Telnyx), and ``outbound_encoding`` is what we *receive out of
    # the call* (Telnyx → Dograh).
    serializer = TelnyxFrameSerializer(
        stream_id=stream_id,
        call_control_id=call_control_id,
        api_key=api_key,
        inbound_encoding="PCMU",  # Dograh → Telnyx; matches stream_bidirectional_codec
        outbound_encoding=encoding,  # Telnyx → Dograh; from media_format.encoding
        transfer_strategy=TelnyxConferenceStrategy(),
        hangup_strategy=TelnyxHangupStrategy(),
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
