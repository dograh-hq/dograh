"""Transport factories for non-telephony pipelines.

Telephony transports live in their respective ``api.services.telephony.providers/<name>/transport.py``.
This module hosts only the shared, non-telephony transports (WebRTC, internal/LoopTalk).
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
    vad_config: dict | None = None,
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


def create_internal_transport(
    workflow_run_id: int,
    audio_config: AudioConfig,
    latency_seconds: float = 0.0,
    vad_config: dict | None = None,
    ambient_noise_config: dict | None = None,
):
    """Create an internal transport for agent-to-agent connections (LoopTalk).

    Args:
        workflow_run_id: ID of the workflow run for turn analyzer context
        audio_config: Audio configuration for the transport
        latency_seconds: Network latency to simulate

    Returns:
        InternalTransport instance configured with turn analyzer
    """
    pass
    # Commented out because looptalk coming in the regular import flow
    # was causing issue. May be move this to looptalk/orchestrator.py
