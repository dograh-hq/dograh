"""Vonage transport factory."""

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from .serializers import VonageFrameSerializer


async def create_transport(
    websocket,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    *,
    vad_config: dict | None = None,
    ambient_noise_config: dict | None = None,
    call_uuid: str,
):
    """Create a transport for Vonage connections."""
    config = await db_client.get_configuration(
        organization_id, OrganizationConfigurationKey.TELEPHONY_CONFIGURATION.value
    )

    if not config or not config.value:
        raise ValueError(
            f"Vonage credentials not configured for organization {organization_id}"
        )

    if config.value.get("provider") != "vonage":
        raise ValueError(
            f"Expected Vonage provider, got {config.value.get('provider')}"
        )

    application_id = config.value.get("application_id")
    private_key = config.value.get("private_key")

    if not application_id or not private_key:
        raise ValueError(
            f"Incomplete Vonage configuration for organization {organization_id}"
        )

    serializer = VonageFrameSerializer(
        call_uuid=call_uuid,
        application_id=application_id,
        private_key=private_key,
        params=VonageFrameSerializer.InputParams(
            vonage_sample_rate=audio_config.transport_in_sample_rate,
            sample_rate=audio_config.pipeline_sample_rate,
        ),
    )

    mixer = await build_audio_out_mixer(
        audio_config.transport_out_sample_rate, ambient_noise_config
    )

    # Vonage uses binary WebSocket mode, not text
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
