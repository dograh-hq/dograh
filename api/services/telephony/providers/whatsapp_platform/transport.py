"""WhatsApp Platform transport factory.

Creates a FastAPIWebsocketTransport configured for raw 16kHz PCM audio
from the WhatsApp bridge. The bridge connects directly to Dograh's
/api/v1/audio-stream/{run_id} WebSocket endpoint.
"""

from typing import Any
from fastapi import WebSocket

from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
from api.services.pipecat.transport_params import realtime_param_overrides
from api.services.telephony.factory import load_credentials_for_transport
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from .serializers import WhatsAppPlatformFrameSerializer
from .strategies import WhatsAppHangupStrategy, WhatsAppTransferStrategy


async def create_transport(
    websocket: WebSocket,
    workflow_run_id: int,
    audio_config: AudioConfig,
    organization_id: int,
    *,
    ambient_noise_config: dict | None = None,
    telephony_configuration_id: int | None = None,
    **kwargs: Any,
) -> FastAPIWebsocketTransport:
    """
    Build a Pipecat FastAPIWebsocketTransport for the WhatsApp bridge.

    The bridge (Node.js dograh-bridge.js) will:
      1. Open a WebSocket to /api/v1/audio-stream/{run_id}
      2. Send 16kHz mono PCM (customer mic audio) as binary frames
      3. Receive 16kHz mono PCM (TTS audio) as binary frames

    Audio parameters are intentionally fixed at 16kHz mono to match
    what the bridge resamples to — no negotiation needed.
    """

    # Load provider credentials (platform_api_url, webhook_secret)
    credentials = await load_credentials_for_transport(
        organization_id=organization_id,
        telephony_configuration_id=telephony_configuration_id,
        expected_provider="whatsapp_platform",
    )

    platform_api_url = credentials.get("platform_api_url", "")
    webhook_secret   = credentials.get("webhook_secret", "")

    # Build the serializer — raw binary PCM, no framing
    serializer = WhatsAppPlatformFrameSerializer()

    # Strategy context passed to transfer/hangup at runtime
    strategy_context = {
        "platform_api_url": platform_api_url,
        "webhook_secret":   webhook_secret,
        "workflow_run_id":  workflow_run_id,
        # wa_call_id is injected at runtime from the run's initial_context
    }

    audio_out_mixer = await build_audio_out_mixer(
        audio_out_sample_rate=WhatsAppPlatformFrameSerializer.SAMPLE_RATE,
        ambient_noise_config=ambient_noise_config,
    )

    params = FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        add_wav_header=False,          # raw PCM — no WAV header
        audio_out_sample_rate=WhatsAppPlatformFrameSerializer.SAMPLE_RATE,
        audio_in_sample_rate=WhatsAppPlatformFrameSerializer.SAMPLE_RATE,
        serializer=serializer,
        audio_out_mixer=audio_out_mixer,
        transfer_strategy=WhatsAppTransferStrategy(),
        hangup_strategy=WhatsAppHangupStrategy(),
        transfer_context=strategy_context,
        hangup_context=strategy_context,
        **realtime_param_overrides(kwargs.get("is_realtime", False)),
    )

    return FastAPIWebsocketTransport(websocket=websocket, params=params)
