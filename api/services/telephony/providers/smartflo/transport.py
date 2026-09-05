"""Smartflo transport factory."""

from fastapi import WebSocket
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
from api.services.pipecat.transport_params import realtime_param_overrides

from .serializers import SmartfloFrameSerializer


async def create_transport(
    websocket: WebSocket,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    *,
    ambient_noise_config: dict | None = None,
    telephony_configuration_id: int | None = None,
    is_realtime: bool = False,
    stream_sid: str = "smartflo_stream",
    call_sid: str = "",
    encoding: str = "audio/x-mulaw",
    **kwargs,
):
    """Create a transport for Smartflo WebSocket connections."""
    serializer = SmartfloFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        sample_rate=audio_config.transport_in_sample_rate,
        encoding=encoding,
    )

    mixer = await build_audio_out_mixer(
        audio_config.transport_out_sample_rate,
        ambient_noise_config,
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
            **realtime_param_overrides(is_realtime),
        ),
    )
