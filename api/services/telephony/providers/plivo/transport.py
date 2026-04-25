"""Plivo transport factory."""

from fastapi import WebSocket

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from .serializers import PlivoFrameSerializer


async def create_transport(
    websocket: WebSocket,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    *,
    vad_config: dict | None = None,
    ambient_noise_config: dict | None = None,
    stream_id: str,
    call_id: str,
):
    """Create a transport for Plivo connections."""
    config = await db_client.get_configuration(
        organization_id, OrganizationConfigurationKey.TELEPHONY_CONFIGURATION.value
    )

    if not config or not config.value:
        raise ValueError(
            f"Plivo credentials not configured for organization {organization_id}"
        )

    if config.value.get("provider") != "plivo":
        raise ValueError(f"Expected Plivo provider, got {config.value.get('provider')}")

    auth_id = config.value.get("auth_id")
    auth_token = config.value.get("auth_token")

    if not auth_id or not auth_token:
        raise ValueError(
            f"Incomplete Plivo configuration for organization {organization_id}"
        )

    serializer = PlivoFrameSerializer(
        stream_id=stream_id,
        call_id=call_id,
        auth_id=auth_id,
        auth_token=auth_token,
        params=PlivoFrameSerializer.InputParams(
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
