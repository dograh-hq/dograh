"""Twilio transport factory."""

from fastapi import WebSocket

from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
from api.services.telephony.factory import load_credentials_for_transport
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from .serializers import TwilioFrameSerializer
from .strategies import TwilioConferenceStrategy, TwilioHangupStrategy


async def create_transport(
    websocket: WebSocket,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    *,
    ambient_noise_config: dict | None = None,
    telephony_configuration_id: int | None = None,
    stream_sid: str,
    call_sid: str,
):
    """Create a transport for Twilio connections."""
    config = await load_credentials_for_transport(
        organization_id, telephony_configuration_id, expected_provider="twilio"
    )

    account_sid = config.get("account_sid")
    auth_token = config.get("auth_token")

    if not account_sid or not auth_token:
        raise ValueError(
            f"Incomplete Twilio configuration for organization {organization_id}"
        )

    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=account_sid,
        auth_token=auth_token,
        transfer_strategy=TwilioConferenceStrategy(),
        hangup_strategy=TwilioHangupStrategy(),
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
