"""Tone (Exotel) transport factory."""

from fastapi import WebSocket
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
from api.services.pipecat.transport_params import realtime_param_overrides
from api.services.telephony.factory import load_credentials_for_transport

from .serializers import ExotelFrameSerializer


async def create_transport(
    websocket: WebSocket,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    *,
    ambient_noise_config: dict | None = None,
    telephony_configuration_id: int | None = None,
    is_realtime: bool = False,
    stream_sid: str,
    call_sid: str | None = None,
):
    """Create a FastAPIWebsocketTransport for a Tone/Exotel call."""
    config = await load_credentials_for_transport(
        organization_id, telephony_configuration_id, expected_provider="tone"
    )

    api_key = config.get("api_key")
    if not api_key:
        raise ValueError(
            f"Incomplete Tone configuration for organization {organization_id}"
        )

    serializer = ExotelFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        params=ExotelFrameSerializer.InputParams(
            exotel_sample_rate=8000,
            sample_rate=audio_config.pipeline_sample_rate,
        ),
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
            **realtime_param_overrides(is_realtime),
        ),
    )
