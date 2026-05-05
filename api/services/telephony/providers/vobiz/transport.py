"""Vobiz transport factory.

Vobiz uses Plivo-compatible WebSocket protocol:
- MULAW audio at 8kHz (same as Twilio)
- Base64-encoded audio in JSON messages
"""

from fastapi import WebSocket
from loguru import logger

from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
from api.services.telephony.factory import load_credentials_for_transport
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from .serializers import VobizFrameSerializer


async def create_transport(
    websocket: WebSocket,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    *,
    ambient_noise_config: dict | None = None,
    telephony_configuration_id: int | None = None,
    stream_id: str,
    call_id: str,
):
    """Create a transport for Vobiz connections."""
    logger.info(
        f"[run {workflow_run_id}] Creating Vobiz transport - "
        f"stream_id={stream_id}, call_id={call_id}"
    )

    config = await load_credentials_for_transport(
        organization_id, telephony_configuration_id, expected_provider="vobiz"
    )

    auth_id = config.get("auth_id")
    auth_token = config.get("auth_token")

    if not auth_id or not auth_token:
        raise ValueError(
            f"Incomplete Vobiz configuration for organization {organization_id}"
        )

    serializer = VobizFrameSerializer(
        stream_id=stream_id,
        call_id=call_id,
        auth_id=auth_id,
        auth_token=auth_token,
        params=VobizFrameSerializer.InputParams(
            vobiz_sample_rate=8000,
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
        ),
    )

    logger.info(f"[run {workflow_run_id}] Vobiz transport created successfully")
    return transport
