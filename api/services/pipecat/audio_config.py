"""Audio configuration for pipeline components.

This module provides centralized audio configuration to ensure consistent
sample rates across all pipeline components and proper coordination between
transport serializers, VAD, and audio buffers.
"""

from dataclasses import dataclass
from typing import Optional

from loguru import logger


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
    buffer_size_seconds: float = 5.0  # This is how frequenly we will call merge_auido
    max_recording_duration_seconds: float = 300.0  # 5 minutes max recording duration

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

    @property
    def buffer_size_bytes(self) -> int:
        """Calculate buffer size in bytes based on pipeline sample rate."""
        # 2 bytes per sample (16-bit PCM)
        return int(self.pipeline_sample_rate * 2 * self.buffer_size_seconds)

    @property
    def buffer_size_samples(self) -> int:
        """Calculate buffer size in samples based on pipeline sample rate."""
        return int(self.pipeline_sample_rate * self.buffer_size_seconds)

    @property
    def max_recording_bytes(self) -> int:
        """Calculate max recording size in bytes based on pipeline sample rate and duration."""
        # 2 bytes per sample (16-bit PCM)
        return int(self.pipeline_sample_rate * 2 * self.max_recording_duration_seconds)


def create_audio_config(transport_type: str, *, is_realtime: bool = False) -> AudioConfig:
    """Create audio configuration for a given transport.

    Telephony providers contribute their wire-format sample rate through the
    provider registry (``ProviderSpec.transport_sample_rate``); WebRTC modes
    use 16 kHz (transports handle resampling from/to 24 kHz). The remaining
    AudioConfig fields are derived from the chosen rate.

    Args:
        transport_type: Transport identifier (e.g. ``"plivo"``, ``"twilio"``).
        is_realtime: When *True* the pipeline will use a realtime/multimodal
            LLM (e.g. Gemini Live) that handles STT internally.  For sub-16 kHz
            telephony transports the internal pipeline rate is lifted to 16 kHz
            so the model receives wideband audio.  When *False* (the default)
            a separate STT service is used, and the pipeline rate stays at the
            wire rate to avoid a sample-rate mismatch between the audio frames
            and the rate declared to the STT provider.
    """
    # Defer registry import to avoid an import cycle: the registry is imported
    # by every telephony provider package at startup.
    from api.enums import WorkflowRunMode
    from api.services.telephony import registry

    telephony_spec = registry.get_optional(transport_type)
    if telephony_spec is not None:
        rate = telephony_spec.transport_sample_rate
    elif transport_type in (
        WorkflowRunMode.WEBRTC.value,
        WorkflowRunMode.SMALLWEBRTC.value,
    ):
        rate = 16000
    else:
        logger.warning(
            f"Unknown transport type: {transport_type}, using default config"
        )
        rate = 16000

    # For realtime/multimodal flows on telephony transports with a sub-16 kHz
    # wire rate (e.g. Plivo/Twilio at 8 kHz), keep the transport wire rates
    # as-is but run the internal pipeline at 16 kHz.  The SOXR serializer
    # automatically upsamples 8→16 kHz on inbound audio so that Gemini Live
    # receives "audio/pcm;rate=16000" instead of the narrowband
    # "audio/pcm;rate=8000" it was getting before.  Gemini is trained on
    # 16 kHz+ audio; sending 8 kHz caused spectral mismatch, poor
    # transcription accuracy, and significantly longer processing times
    # (5–40 s STT spans).  Outbound audio (Gemini outputs 24 kHz) is already
    # downsampled to the wire rate by the serializer, so Plivo still receives
    # standard 8 kHz µ-law.
    #
    # For non-realtime flows the pipeline rate must equal the transport rate
    # because separate STT services (Deepgram, etc.) are constructed with
    # sample_rate=transport_in_sample_rate.  Lifting the pipeline rate without
    # updating the declared rate would cause the provider to interpret the
    # audio at double speed.
    REALTIME_PIPELINE_RATE = 16000
    if is_realtime and rate < REALTIME_PIPELINE_RATE:
        pipeline_rate = REALTIME_PIPELINE_RATE
    else:
        pipeline_rate = rate

    return AudioConfig(
        transport_in_sample_rate=rate,          # wire rate unchanged (e.g. 8000 for Plivo)
        transport_out_sample_rate=rate,         # wire rate unchanged (e.g. 8000 for Plivo)
        vad_sample_rate=rate,                   # VAD operates on wire-rate audio
        pipeline_sample_rate=pipeline_rate,     # 16 kHz for realtime, wire rate otherwise
    )
