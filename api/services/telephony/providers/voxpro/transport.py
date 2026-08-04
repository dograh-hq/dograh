"""VoxPro transport factory.

VoxPro streams the Plivo/Twilio-standard protocol (mu-law 8 kHz, base64 JSON),
so the transport reuses the published Plivo serializer.
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

from .serializers import VoxProFrameSerializer


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
    """Create a transport for VoxPro connections."""
    logger.info(
        f"[run {workflow_run_id}] Creating VoxPro transport - "
        f"stream_id={stream_id}, call_id={call_id}"
    )

    # Ensure the org actually has a voxpro config (validates + scopes).
    await load_credentials_for_transport(
        organization_id, telephony_configuration_id, expected_provider="voxpro"
    )

    serializer = VoxProFrameSerializer(
        stream_id=stream_id,
        call_id=call_id,
        params=VoxProFrameSerializer.InputParams(
            plivo_sample_rate=8000,
            sample_rate=audio_config.pipeline_sample_rate,
            # VoxPro terminates the call through its own connector (Asterisk),
            # not via the serializer's Plivo REST hang-up — so no auth_id/auth_token
            # are needed and auto_hang_up must be disabled (else __init__ raises).
            auto_hang_up=False,
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

    logger.info(f"[run {workflow_run_id}] VoxPro transport created successfully")
    return transport
