#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Internal frame serializer for agent-to-agent communication."""

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    StartFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer, FrameSerializerType


class InternalFrameSerializer(FrameSerializer):
    """Serializer for InternalTransport that filters frames between agents.

    This serializer ensures only audio frames are passed between agents,
    preventing control frames from creating infinite loops.
    """

    @property
    def type(self) -> FrameSerializerType:
        """Internal transport uses binary frames."""
        return FrameSerializerType.BINARY

    async def setup(self, frame: StartFrame):
        """No setup required for internal transport."""
        pass

    async def serialize(self, frame: Frame) -> bytes | None:
        """Only serialize audio frames for transmission between agents."""
        # Only pass audio frames between agents
        if isinstance(frame, OutputAudioRawFrame):
            # Use a fixed-size header to avoid parsing issues with binary data
            # Format: "AUDIO" (5 bytes) + sample_rate (4 bytes) + num_channels (2 bytes) + audio data
            header = b"AUDIO"
            sample_rate_bytes = frame.sample_rate.to_bytes(4, byteorder="big")
            num_channels_bytes = frame.num_channels.to_bytes(2, byteorder="big")

            serialized = header + sample_rate_bytes + num_channels_bytes + frame.audio
            return serialized

        # Don't pass control frames between agents
        return None

    async def deserialize(self, data: bytes) -> Frame | None:
        """Deserialize audio frames from partner agent."""
        if data.startswith(b"AUDIO"):
            try:
                # Fixed-size header parsing
                # Header: "AUDIO" (5 bytes) + sample_rate (4 bytes) + num_channels (2 bytes)
                if len(data) < 11:  # Minimum size for header
                    logger.error(
                        f"InternalSerializer: Data too short for header: {len(data)} bytes"
                    )
                    return None

                # Extract fixed-size fields
                # Skip header validation - we already checked startswith(b"AUDIO")
                sample_rate = int.from_bytes(data[5:9], byteorder="big")
                num_channels = int.from_bytes(data[9:11], byteorder="big")

                # Extract audio data - everything after the header
                audio_data = data[11:]

                # Check if audio data length is valid
                if len(audio_data) % 2 != 0:
                    logger.warning(
                        f"InternalSerializer: Audio data has odd length: {len(audio_data)}"
                    )

                # Convert to InputAudioRawFrame for the receiving agent
                return InputAudioRawFrame(
                    audio=audio_data, num_channels=num_channels, sample_rate=sample_rate
                )
            except Exception as e:
                logger.error(f"Failed to deserialize audio frame: {e}")
                return None

        return None
