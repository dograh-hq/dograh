"""Transport factories for non-telephony pipelines.

Telephony transports live in their respective ``api.services.telephony.providers/<name>/transport.py``.
This module hosts only the shared, non-telephony transports (WebRTC).
"""

from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.audio_mixer import build_audio_out_mixer
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport


async def create_webrtc_transport(
    webrtc_connection: SmallWebRTCConnection,
    workflow_run_id: int,
    audio_config: AudioConfig,
    ambient_noise_config: dict | None = None,
):
    """Create a transport for WebRTC connections."""
    mixer = await build_audio_out_mixer(
        audio_config.transport_out_sample_rate, ambient_noise_config
    )

    return SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=audio_config.transport_in_sample_rate,
            audio_out_sample_rate=audio_config.transport_out_sample_rate,
            audio_out_mixer=mixer,
        ),
    )
