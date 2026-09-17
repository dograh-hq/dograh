"""WhatsApp transport factory."""

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

from .serializers import WhatsAppFrameSerializer
from .strategies import WhatsAppHangupStrategy, WhatsAppTransferStrategy


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
    """Create a transport for WhatsApp connections."""
    config = await load_credentials_for_transport(
        organization_id, telephony_configuration_id, expected_provider="whatsapp"
    )

    access_token = config.get("access_token")
    phone_number_id = config.get("phone_number_id")

    if not access_token or not phone_number_id:
        raise ValueError(
            f"Incomplete WhatsApp configuration for organization {organization_id}"
        )

    serializer = WhatsAppFrameSerializer(
        call_id=call_id,
        phone_number_id=phone_number_id,
        access_token=access_token,
        transfer_strategy=WhatsAppTransferStrategy(),
        hangup_strategy=WhatsAppHangupStrategy(),
        sample_rate=audio_config.transport_in_sample_rate,
    )

    mixer = await build_audio_out_mixer(
        audio_config.transport_out_sample_rate, ambient_noise_config
    )

    logger.info(
        f"Created WhatsApp transport for call_id={call_id}, "
        f"workflow_run_id={workflow_run_id}"
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
