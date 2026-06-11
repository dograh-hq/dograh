"""VoiceLink transport factory.

VoiceLink uses a Twilio-media-streams-style WebSocket protocol:
- G.711 A-law audio at 8 kHz (NOT µ-law)
- Base64-encoded audio in JSON messages
"""

from fastapi import WebSocket
from loguru import logger
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
from api.services.pipecat.transport_params import realtime_param_overrides
from api.services.telephony.factory import load_credentials_for_transport

from .serializers import VoiceLinkFrameSerializer


async def create_transport(
    websocket: WebSocket,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    *,
    ambient_noise_config: dict | None = None,
    telephony_configuration_id: int | None = None,
    is_realtime: bool = False,
    stream_id: str,
    call_id: str,
):
    """Create a transport for VoiceLink connections."""
    logger.info(
        f"[run {workflow_run_id}] Creating VoiceLink transport - "
        f"stream_sid={stream_id}, call_sid={call_id}"
    )

    # The serializer needs no credentials (VoiceLink has no per-call REST
    # hangup), but resolving the config validates the run is bound to a
    # VoiceLink configuration for this organization.
    await load_credentials_for_transport(
        organization_id, telephony_configuration_id, expected_provider="voicelink"
    )

    serializer = VoiceLinkFrameSerializer(
        stream_sid=stream_id,
        call_sid=call_id,
        params=VoiceLinkFrameSerializer.InputParams(
            voicelink_sample_rate=8000,
            sample_rate=audio_config.pipeline_sample_rate,
        ),
    )

    mixer = await build_audio_out_mixer(
        audio_config.transport_out_sample_rate, ambient_noise_config
    )

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=audio_config.transport_in_sample_rate,
            audio_out_sample_rate=audio_config.transport_out_sample_rate,
            audio_out_mixer=mixer,
            serializer=serializer,
            **realtime_param_overrides(is_realtime),
        ),
    )

    logger.info(f"[run {workflow_run_id}] VoiceLink transport created successfully")
    return transport
