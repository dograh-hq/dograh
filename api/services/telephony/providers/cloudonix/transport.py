"""Cloudonix transport factory."""

from fastapi import WebSocket

from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
from api.services.telephony.factory import load_credentials_for_transport
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
    telephony_configuration_id: int | None = None,
    call_id: str,
    stream_sid: str,
    bearer_token: str | None = None,
    domain_id: str | None = None,
):
    """Create a transport for Cloudonix connections.

    When ``bearer_token`` and ``domain_id`` are both supplied, they are used
    directly and no DB lookup is performed — this is the agent-stream path
    where the caller brings credentials inline. Otherwise credentials are
    resolved from the org's stored telephony configuration.
    """
    if not (bearer_token and domain_id):
        config = await load_credentials_for_transport(
            organization_id, telephony_configuration_id, expected_provider="cloudonix"
        )
        bearer_token = config.get("bearer_token")
        domain_id = config.get("domain_id")

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
