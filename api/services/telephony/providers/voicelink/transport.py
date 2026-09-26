"""VoiceLink transport factory."""

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
    start_message: str,
):
    await load_credentials_for_transport(
        organization_id, telephony_configuration_id, expected_provider="voicelink"
    )

    serializer = VoiceLinkFrameSerializer(
        params=VoiceLinkFrameSerializer.InputParams(
            sample_rate=audio_config.pipeline_sample_rate,
        ),
    )
    # The provider already consumed VoiceLink's ``start`` frame during the
    # handshake. Replay it so the serializer learns the stream id and the audio
    # format VoiceLink is actually sending before the first media frame.
    await serializer.deserialize(start_message)

    logger.info(
        f"[run {workflow_run_id}] Creating VoiceLink transport - "
        f"stream_sid={serializer.stream_sid}, call_sid={serializer.call_sid}"
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
