"""TryVox transport factory."""

from fastapi import WebSocket
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
from api.services.pipecat.transport_params import realtime_param_overrides
from api.services.telephony.factory import load_credentials_for_transport

from .serializers import TryVoxFrameSerializer


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
    """Create a bidirectional binary PCM transport for TryVox."""
    config = await load_credentials_for_transport(
        organization_id, telephony_configuration_id, expected_provider="tryvox"
    )
    auth_id = config.get("auth_id")
    auth_token = config.get("auth_token")
    if not auth_id or not auth_token:
        raise ValueError(
            f"Incomplete TryVox configuration for organization {organization_id}"
        )

    serializer = TryVoxFrameSerializer(
        call_id=call_id,
        auth_id=auth_id,
        auth_token=auth_token,
        api_base_url=config.get("api_base_url") or "https://api.tryvox.io",
        params=TryVoxFrameSerializer.InputParams(
            tryvox_sample_rate=audio_config.transport_in_sample_rate,
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
