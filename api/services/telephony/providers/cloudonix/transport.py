"""Cloudonix transport factory."""

from fastapi import WebSocket

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from .serializers import CloudonixFrameSerializer
from .strategies import CloudonixHangupStrategy


async def create_transport(
    websocket: WebSocket,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    *,
    vad_config: dict | None = None,
    ambient_noise_config: dict | None = None,
    call_id: str,
    stream_sid: str,
):
    """Create a transport for Cloudonix connections."""
    config = await db_client.get_configuration(
        organization_id, OrganizationConfigurationKey.TELEPHONY_CONFIGURATION.value
    )

    if not config or not config.value:
        raise ValueError(
            f"Cloudonix credentials not configured for organization {organization_id}"
        )

    if config.value.get("provider") != "cloudonix":
        raise ValueError(
            f"Expected Cloudonix provider, got {config.value.get('provider')}"
        )

    bearer_token = config.value.get("bearer_token")
    domain_id = config.value.get("domain_id")

    if not bearer_token or not domain_id:
        raise ValueError(
            f"Incomplete Cloudonix configuration for organization {organization_id}. "
            f"Required: bearer_token, domain_id"
        )

    serializer = CloudonixFrameSerializer(
        call_id=call_id,
        stream_sid=stream_sid,
        domain_id=domain_id,
        bearer_token=bearer_token,
        hangup_strategy=CloudonixHangupStrategy(),
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
            audio_out_10ms_chunks=2,
        ),
    )
