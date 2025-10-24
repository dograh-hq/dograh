"""
Audio configuration for pipeline components.

This module provides centralized audio configuration to ensure consistent
sample rates across all pipeline components and proper coordination between
transport serializers, VAD, and audio buffers.
"""

from dataclasses import dataclass
from typing import Optional

from loguru import logger

from api.enums import WorkflowRunMode


@dataclass
class AudioConfig:
    """Centralized audio configuration for the pipeline.

    Note: Pipeline is limited to 16kHz maximum to support VAD.
    Transports handle resampling from/to higher rates (24kHz, 48kHz).

    Attributes:
        transport_in_sample_rate: Sample rate of incoming audio from transport (after resampling)
        transport_out_sample_rate: Sample rate of outgoing audio to transport (before resampling)
        vad_sample_rate: Sample rate for VAD processing (8000 or 16000)
        pipeline_sample_rate: Internal pipeline processing sample rate (max 16000)
        buffer_size_seconds: Audio buffer size in seconds
    """

    transport_in_sample_rate: int
    transport_out_sample_rate: int
    vad_sample_rate: int = 16000  # VAD typically resamples internally
    pipeline_sample_rate: Optional[int] = None  # If None, uses transport rates
    buffer_size_seconds: float = 1.0  # This is how frequenly we will call merge_auido

    def __post_init__(self):
        # Validate VAD sample rate
        if self.vad_sample_rate not in [8000, 16000]:
            raise ValueError(
                f"VAD sample rate must be 8000 or 16000, got {self.vad_sample_rate}"
            )

        # Set pipeline sample rate to transport out rate if not specified
        if self.pipeline_sample_rate is None:
            self.pipeline_sample_rate = min(self.transport_out_sample_rate, 16000)

        # Ensure pipeline sample rate doesn't exceed 16kHz (VAD limitation)
        if self.pipeline_sample_rate > 16000:
            logger.warning(
                f"Pipeline sample rate {self.pipeline_sample_rate} exceeds 16kHz limit, "
                f"capping at 16kHz. Transport will handle resampling."
            )
            self.pipeline_sample_rate = 16000

        # Log configuration for auditing
        logger.info(
            f"AudioConfig initialized: "
            f"transport_in={self.transport_in_sample_rate}Hz, "
            f"transport_out={self.transport_out_sample_rate}Hz, "
            f"vad={self.vad_sample_rate}Hz, "
            f"pipeline={self.pipeline_sample_rate}Hz, "
            f"buffer={self.buffer_size_seconds}s"
        )

    @property
    def buffer_size_bytes(self) -> int:
        """Calculate buffer size in bytes based on pipeline sample rate."""
        # 2 bytes per sample (16-bit PCM)
        return int(self.pipeline_sample_rate * 2 * self.buffer_size_seconds)

    @property
    def buffer_size_samples(self) -> int:
        """Calculate buffer size in samples based on pipeline sample rate."""
        return int(self.pipeline_sample_rate * self.buffer_size_seconds)


def create_audio_config(transport_type: str) -> AudioConfig:
    """Create audio configuration based on transport type.

    Args:
        transport_type: Type of transport ("webrtc", "twilio", "vonage", "stasis")

    Returns:
        AudioConfig instance with appropriate settings
    """
    if transport_type in (WorkflowRunMode.STASIS.value, WorkflowRunMode.TWILIO.value):
        return AudioConfig(
            transport_in_sample_rate=8000,
            transport_out_sample_rate=8000,
            vad_sample_rate=8000,  # Use matching VAD rate
            pipeline_sample_rate=8000,  # Keep at 8kHz to avoid resampling
            buffer_size_seconds=1.0,
        )
    elif transport_type == WorkflowRunMode.VONAGE.value:
        # Vonage uses 16kHz Linear PCM
        return AudioConfig(
            transport_in_sample_rate=16000,
            transport_out_sample_rate=16000,
            vad_sample_rate=16000,  # Use matching VAD rate
            pipeline_sample_rate=16000,  # Keep at 16kHz to avoid resampling
            buffer_size_seconds=1.0,
        )
    elif transport_type in [
        WorkflowRunMode.WEBRTC.value,
        WorkflowRunMode.SMALLWEBRTC.value,
    ]:
        # WebRTC typically uses 24kHz or 48kHz, but we limit pipeline to 16kHz
        # The transport will handle resampling between 24kHz and 16kHz
        return AudioConfig(
            transport_in_sample_rate=16000,  # Transport will resample from 24kHz
            transport_out_sample_rate=16000,  # Transport will resample to 24kHz
            vad_sample_rate=16000,  # VAD native rate
            pipeline_sample_rate=16000,  # Keep pipeline at 16kHz
            buffer_size_seconds=1.0,
        )
    else:
        # Default configuration
        logger.warning(
            f"Unknown transport type: {transport_type}, using default config"
        )
        return AudioConfig(
            transport_in_sample_rate=16000,
            transport_out_sample_rate=16000,
            vad_sample_rate=16000,
            pipeline_sample_rate=16000,
            buffer_size_seconds=1.0,
        )
