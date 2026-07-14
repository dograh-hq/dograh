import asyncio
import io
import wave
from copy import deepcopy
from datetime import UTC, datetime
from typing import List, Optional

from loguru import logger

from api.services.pipecat.realtime_feedback_events import (
    realtime_feedback_event_sort_key,
    stamp_realtime_feedback_event,
)
from api.utils.transcript import generate_transcript_text as _generate_transcript_text
from pipecat.utils.enums import RealtimeFeedbackType


class InMemoryAudioBuffer:
    """Buffer audio data in memory during a call, then encode to WAV bytes on disconnect."""

    def __init__(self, workflow_run_id: int, sample_rate: int, num_channels: int = 1):
        self._workflow_run_id = workflow_run_id
        self._sample_rate = sample_rate
        self._num_channels = num_channels
        self._chunks: List[bytes] = []
        self._lock = asyncio.Lock()
        self._total_size = 0
        self._max_size = 100 * 1024 * 1024  # 100MB limit

    async def append(self, pcm_data: bytes):
        """Append PCM audio data to the buffer."""
        async with self._lock:
            if self._total_size + len(pcm_data) > self._max_size:
                logger.error(
                    f"Audio buffer size limit exceeded for workflow {self._workflow_run_id}. "
                    f"Current: {self._total_size}, Attempted to add: {len(pcm_data)}"
                )
                raise MemoryError("Audio buffer size limit exceeded")
            self._chunks.append(pcm_data)
            self._total_size += len(pcm_data)
            logger.trace(
                f"Appended {len(pcm_data)} bytes to audio buffer. Total size: {self._total_size}"
            )

    async def to_wav_bytes(self) -> bytes:
        """Encode the buffered PCM data as an in-memory WAV file."""
        async with self._lock:
            chunks = list(self._chunks)

        def _encode() -> bytes:
            wav_io = io.BytesIO()
            with wave.open(wav_io, "wb") as wf:
                wf.setnchannels(self._num_channels)
                wf.setsampwidth(2)  # 16-bit audio
                wf.setframerate(self._sample_rate)

                # Concatenate all chunks
                for chunk in chunks:
                    wf.writeframes(chunk)
            return wav_io.getvalue()

        # Encoding is mostly memcpy but can touch ~100MB; keep it off the event loop
        data = await asyncio.to_thread(_encode)
        logger.info(
            f"Encoded {self._total_size} bytes of audio to {len(data)} WAV bytes "
            f"for workflow {self._workflow_run_id}"
        )
        return data

    @property
    def is_empty(self) -> bool:
        """Check if the buffer is empty."""
        return len(self._chunks) == 0

    @property
    def size(self) -> int:
        """Get the total size of buffered data."""
        return self._total_size


class InMemoryRecordingBuffers:
    """Holds the mixed recording plus aligned user and bot mono tracks."""

    def __init__(self, workflow_run_id: int, sample_rate: int, num_channels: int = 1):
        self.mixed = InMemoryAudioBuffer(
            workflow_run_id=workflow_run_id,
            sample_rate=sample_rate,
            num_channels=num_channels,
        )
        self.user = InMemoryAudioBuffer(
            workflow_run_id=workflow_run_id,
            sample_rate=sample_rate,
            num_channels=1,
        )
        self.bot = InMemoryAudioBuffer(
            workflow_run_id=workflow_run_id,
            sample_rate=sample_rate,
            num_channels=1,
        )


class InMemoryLogsBuffer:
    """Buffer real-time feedback events in memory during a call, then save to workflow run logs."""

    def __init__(self, workflow_run_id: int):
        self._workflow_run_id = workflow_run_id
        self._events: List[dict] = []
        self._current_turn: Optional[int] = None
        self._current_node_id: Optional[str] = None
        self._current_node_name: Optional[str] = None

    def set_current_node(self, node_id: str, node_name: str):
        """Set the current node ID and name to be injected into subsequent events."""
        self._current_node_id = node_id
        self._current_node_name = node_name

    @property
    def current_node_id(self) -> Optional[str]:
        """Get the current node ID."""
        return self._current_node_id

    @property
    def current_node_name(self) -> Optional[str]:
        """Get the current node name."""
        return self._current_node_name

    def set_current_turn(self, turn: int) -> None:
        """Set the fallback turn for non-transcript events."""
        self._current_turn = turn

    async def append(
        self,
        event: dict,
        *,
        timestamp: Optional[str] = None,
        turn: Optional[int] = None,
        node_id: Optional[str] = None,
        node_name: Optional[str] = None,
        use_current_node: bool = True,
    ):
        """Append an immutable event with optional correlation metadata."""
        if use_current_node:
            node_id = self._current_node_id if node_id is None else node_id
            node_name = self._current_node_name if node_name is None else node_name
        timestamped_event = stamp_realtime_feedback_event(
            deepcopy(event),
            timestamp=timestamp or datetime.now(UTC).isoformat(timespec="milliseconds"),
            turn=self._current_turn if turn is None else turn,
            node_id=node_id,
            node_name=node_name,
        )
        self._events.append(timestamped_event)
        logger.trace(
            f"Appended event {event.get('type')} to logs buffer for workflow {self._workflow_run_id}"
        )

    def _sorted_events(self) -> List[dict]:
        # Stable sort by the top-level event timestamp used by the persisted
        # realtime feedback schema. Legacy events without one fall back to their
        # payload timestamp. Events sharing a key retain insertion order.
        return sorted(self._events, key=realtime_feedback_event_sort_key)

    def get_events(self) -> List[dict]:
        """Get all events for final storage, ordered by event timestamp."""
        return self._sorted_events()

    def contains_user_speech(self) -> bool:
        """Return True if any final user transcription event has non-empty text."""
        for event in self._events:
            if (
                event.get("type") == RealtimeFeedbackType.USER_TRANSCRIPTION.value
                and event.get("payload", {}).get("final") is True
                and event.get("payload", {}).get("text")
            ):
                return True
        return False

    def generate_transcript_text(self, *, include_end_timestamps: bool = False) -> str:
        """Generate transcript text from logged events.

        Filters for rtf-user-transcription (final) and rtf-bot-text events,
        formats them as '[timestamp] user/assistant: text\\n'.
        """
        return _generate_transcript_text(
            self._sorted_events(), include_end_timestamps=include_end_timestamps
        )

    @property
    def is_empty(self) -> bool:
        """Check if the buffer is empty."""
        return len(self._events) == 0
