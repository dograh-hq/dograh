"""Papi Voip transport factory (PCM 16 kHz ↔ Pipecat)."""

from fastapi import WebSocket
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
from api.services.pipecat.transport_params import realtime_param_overrides
from api.services.telephony.factory import load_credentials_for_transport

from .serializers import PapiVoipFrameSerializer
from .strategies import PapiVoipHangupStrategy


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
    """Create a transport for Papi Voip media (bridged onto Dograh's media WS)."""
    config = await load_credentials_for_transport(
        organization_id, telephony_configuration_id, expected_provider="papi_voip"
    )

    base_url = (config.get("base_url") or "https://api.papi.api.br").rstrip("/")
    api_key = config.get("api_key")
    instance_id = config.get("instance_id")

    if not api_key or not instance_id:
        raise ValueError(
            f"Incomplete Papi Voip configuration for organization {organization_id}. "
            "Required: api_key, instance_id"
        )

    serializer = PapiVoipFrameSerializer(
        call_id=call_id,
        base_url=base_url,
        api_key=api_key,
        instance_id=instance_id,
        hangup_strategy=PapiVoipHangupStrategy(),
        params=PapiVoipFrameSerializer.InputParams(
            papi_sample_rate=audio_config.transport_in_sample_rate,
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
