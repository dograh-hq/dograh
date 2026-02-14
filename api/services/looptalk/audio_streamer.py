"""
Audio streaming processor for LoopTalk real-time audio monitoring.

This processor captures audio from both actor and adversary agents and streams
it to connected WebRTC clients for real-time monitoring.
"""

import asyncio
from typing import Dict, Set

from loguru import logger
from pipecat.audio.utils import mix_audio
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class LoopTalkAudioStreamer(FrameProcessor):
    """
    Processes audio frames from LoopTalk conversations and streams to WebRTC clients.

    This processor sits in the pipeline and captures all audio frames, then
    forwards them to connected WebRTC clients for real-time monitoring.
    """

    def __init__(
        self,
        test_session_id: str,
        role: str,  # "actor" or "adversary"
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._test_session_id = test_session_id
        self._role = role
        self._listeners: Set[asyncio.Queue] = set()
        self._sample_rate = 16000  # Default sample rate
        self._num_channels = 1

    def add_listener(self, queue: asyncio.Queue):
        """Add a listener queue for streaming audio."""
        self._listeners.add(queue)

    def remove_listener(self, queue: asyncio.Queue):
        """Remove a listener queue."""
        self._listeners.discard(queue)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process audio frames and stream to listeners."""
        await super().process_frame(frame, direction)

        # Capture both input and output audio
        if isinstance(frame, (InputAudioRawFrame, OutputAudioRawFrame)):
            # Extract audio data
            audio_data = frame.audio
            sample_rate = frame.sample_rate
            num_channels = frame.num_channels

            # Store sample rate for reference
            if sample_rate:
                self._sample_rate = sample_rate
            if num_channels:
                self._num_channels = num_channels

            # Stream to all listeners
            if self._listeners and audio_data:
                # Create a packet with metadata
                packet = {
                    "test_session_id": self._test_session_id,
                    "role": self._role,
                    "audio": audio_data,
                    "sample_rate": sample_rate,
                    "num_channels": num_channels,
                    "is_input": isinstance(frame, InputAudioRawFrame),
                }

                # Send to all listeners without blocking
                for queue in list(self._listeners):
                    try:
                        queue.put_nowait(packet)
                    except asyncio.QueueFull:
                        logger.warning(
                            f"Audio queue full for session {self._test_session_id}"
                        )
                    except Exception as e:
                        logger.error(f"Error streaming audio: {e}")
                        self._listeners.discard(queue)
            elif self._listeners and not audio_data:
                logger.warning(
                    f"Audio streamer {self._role} received frame with no audio data"
                )
            elif audio_data and not self._listeners:
                # This is expected early in the session before WebSocket connects
                pass

        # Always forward the frame
        await self.push_frame(frame, direction)


class LoopTalkAudioMixer:
    """
    Mixes audio from actor and adversary streams for combined playback.

    This class manages the mixing of two audio streams (actor and adversary)
    to create a combined audio stream for monitoring.
    """

    def __init__(self, test_session_id: str):
        self._test_session_id = test_session_id
        self._actor_buffer = bytearray()
        self._adversary_buffer = bytearray()
        self._listeners: Set[asyncio.Queue] = set()
        self._sample_rate = 16000
        self._num_channels = 1
        self._buffer_size = 8000  # 0.5 seconds at 16kHz

    def add_listener(self, queue: asyncio.Queue):
        """Add a listener for mixed audio."""
        self._listeners.add(queue)

    def remove_listener(self, queue: asyncio.Queue):
        """Remove a listener."""
        self._listeners.discard(queue)

    async def add_audio(
        self, role: str, audio_data: bytes, sample_rate: int, num_channels: int
    ):
        """Add audio data from actor or adversary."""
        if role == "actor":
            self._actor_buffer.extend(audio_data)
        elif role == "adversary":
            self._adversary_buffer.extend(audio_data)

        # Update audio parameters
        self._sample_rate = sample_rate
        self._num_channels = num_channels

        # Check if we have enough data to mix
        await self._check_and_mix()

    async def _check_and_mix(self):
        """Check buffers and mix audio when enough data is available."""
        # Mix when we have at least buffer_size in both buffers
        while (
            len(self._actor_buffer) >= self._buffer_size
            and len(self._adversary_buffer) >= self._buffer_size
        ):
            # Extract chunks
            actor_chunk = bytes(self._actor_buffer[: self._buffer_size])
            adversary_chunk = bytes(self._adversary_buffer[: self._buffer_size])

            # Remove from buffers
            del self._actor_buffer[: self._buffer_size]
            del self._adversary_buffer[: self._buffer_size]

            # Mix audio
            mixed_audio = mix_audio(actor_chunk, adversary_chunk)

            # Stream to listeners
            if self._listeners and mixed_audio:
                packet = {
                    "test_session_id": self._test_session_id,
                    "role": "mixed",
                    "audio": mixed_audio,
                    "sample_rate": self._sample_rate,
                    "num_channels": self._num_channels,
                    "is_input": False,
                }

                for queue in list(self._listeners):
                    try:
                        queue.put_nowait(packet)
                    except asyncio.QueueFull:
                        logger.warning(
                            f"Mixed audio queue full for session {self._test_session_id}"
                        )
                    except Exception as e:
                        logger.error(f"Error streaming mixed audio: {e}")
                        self._listeners.discard(queue)


# Global registry for audio streamers and mixers
_audio_streamers: Dict[str, Dict[str, LoopTalkAudioStreamer]] = {}
_audio_mixers: Dict[str, LoopTalkAudioMixer] = {}


def get_or_create_audio_streamer(
    test_session_id: str, role: str
) -> LoopTalkAudioStreamer:
    """Get or create an audio streamer for a test session and role."""
    if test_session_id not in _audio_streamers:
        _audio_streamers[test_session_id] = {}

    if role not in _audio_streamers[test_session_id]:
        _audio_streamers[test_session_id][role] = LoopTalkAudioStreamer(
            test_session_id=test_session_id, role=role
        )

    return _audio_streamers[test_session_id][role]


def get_or_create_audio_mixer(test_session_id: str) -> LoopTalkAudioMixer:
    """Get or create an audio mixer for a test session."""
    if test_session_id not in _audio_mixers:
        _audio_mixers[test_session_id] = LoopTalkAudioMixer(test_session_id)

    return _audio_mixers[test_session_id]


def cleanup_audio_streamers(test_session_id: str):
    """Clean up audio streamers and mixers for a test session."""
    if test_session_id in _audio_streamers:
        del _audio_streamers[test_session_id]

    if test_session_id in _audio_mixers:
        del _audio_mixers[test_session_id]

    logger.info(f"Cleaned up audio streamers for test session {test_session_id}")
