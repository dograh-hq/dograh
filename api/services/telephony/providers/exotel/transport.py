"""Exotel transport factory."""

from fastapi import WebSocket
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
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
    stream_id: str,
    call_id: str,
):
    """Create a WebSocket transport for an Exotel call leg."""
    config = await load_credentials_for_transport(
        organization_id, telephony_configuration_id, expected_provider="exotel"
    )

    api_key = config.get("api_key")
    api_token = config.get("api_token")

    if not api_key or not api_token:
        raise ValueError(
            f"Incomplete Exotel configuration for organization {organization_id}"
        )

    # ExotelFrameSerializer is PlivoFrameSerializer under the hood —
    # same μ-law 8 kHz JSON envelope. The auth_id/auth_token params are used
    # by Plivo's serializer for optional mid-call REST calls; Exotel doesn't
    # need them but we pass api_key/api_token for future extensibility.
    serializer = ExotelFrameSerializer(
        stream_id=stream_id,
        call_id=call_id,
        auth_id=api_key,
        auth_token=api_token,
        params=ExotelFrameSerializer.InputParams(
            plivo_sample_rate=8000,
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
        ),
    )
