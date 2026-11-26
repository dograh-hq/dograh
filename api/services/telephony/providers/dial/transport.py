"""Dial transport factory."""

from fastapi import WebSocket
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
from api.services.pipecat.transport_params import realtime_param_overrides
from api.services.telephony.factory import load_credentials_for_transport

from .serializers import DialFrameSerializer


async def create_transport(
    websocket: WebSocket,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    *,
    ambient_noise_config: dict | None = None,
    telephony_configuration_id: int | None = None,
    is_realtime: bool = False,
    call_id: str,
):
    """Create a transport for a Dial Self-Hosted audio connection.

    ``websocket`` is the keepalive wrapper from ``websocket.py``; it answers
    Dial's ``ping_pong`` frames so pipecat only ever sees call traffic.
    Hangup travels in-band (``end_call``), so the serializer needs no
    credentials — loading them here only asserts the run's configuration
    still belongs to Dial.
    """
    await load_credentials_for_transport(
        organization_id, telephony_configuration_id, expected_provider="dial"
    )

    serializer = DialFrameSerializer(call_id=call_id)

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
