import asyncio
import tempfile
import wave
from datetime import UTC, datetime
from typing import List, Optional

from loguru import logger

from api.utils.transcript import generate_transcript_text as _generate_transcript_text
from pipecat.utils.enums import RealtimeFeedbackType


class InMemoryAudioBuffer:
    """Buffer audio data in memory during a call, then write to temp file on disconnect."""

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

    async def write_to_temp_file(self) -> str:
        """Write audio data to a temporary WAV file and return the path."""
        async with self._lock:
            temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            logger.debug(
                f"Writing audio buffer to temp file {temp_file.name} for workflow {self._workflow_run_id}"
            )

            # Write WAV header and PCM data
            with wave.open(temp_file.name, "wb") as wf:
                wf.setnchannels(self._num_channels)
                wf.setsampwidth(2)  # 16-bit audio
                wf.setframerate(self._sample_rate)

                # Concatenate all chunks
                for chunk in self._chunks:
                    wf.writeframes(chunk)

            logger.info(
                f"Successfully wrote {self._total_size} bytes of audio to {temp_file.name}"
            )
            return temp_file.name

    @property
    def is_empty(self) -> bool:
        """Check if the buffer is empty."""
        return len(self._chunks) == 0

    @property
    def size(self) -> int:
        """Get the total size of buffered data."""
        return self._total_size


class InMemoryLogsBuffer:
    """Buffer real-time feedback events in memory during a call, then save to workflow run logs."""

    def __init__(self, workflow_run_id: int):
        self._workflow_run_id = workflow_run_id
        self._events: List[dict] = []
        self._turn_counter = 0
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

    async def append(self, event: dict):
        """Append a feedback event to the buffer with timestamp and current node."""
        # Add timestamp, turn tracking, and current node
        timestamped_event = {
            **event,
            "timestamp": datetime.now(UTC).isoformat(),
            "turn": self._turn_counter,
        }
        if self._current_node_id:
            timestamped_event["node_id"] = self._current_node_id
        if self._current_node_name:
            timestamped_event["node_name"] = self._current_node_name
        self._events.append(timestamped_event)
        logger.trace(
            f"Appended event {event.get('type')} to logs buffer for workflow {self._workflow_run_id}"
        )

    def increment_turn(self):
        """Increment turn counter (called on user transcription completion)."""
        self._turn_counter += 1
        logger.trace(
            f"Incremented turn counter to {self._turn_counter} for workflow {self._workflow_run_id}"
        )

    @staticmethod
    def _event_sort_key(event: dict) -> str:
        payload_ts = event.get("payload", {}).get("timestamp")
        return payload_ts or event.get("timestamp", "")

    def _sorted_events(self) -> List[dict]:
        # Stable sort by the realtime (payload) timestamp when available, falling
        # back to the buffer-append timestamp. Python's sort is stable, so events
        # sharing a key retain their original insertion order — this keeps
        # consecutive bot-text chunks of a single turn contiguous.
        return sorted(self._events, key=self._event_sort_key)

    def get_events(self) -> List[dict]:
        """Get all events for final storage, ordered by realtime timestamp."""
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

    def generate_transcript_text(self) -> str:
        """Generate transcript text from logged events.

        Filters for rtf-user-transcription (final) and rtf-bot-text events,
        formats them as '[timestamp] user/assistant: text\\n'.
        """
        return _generate_transcript_text(self._sorted_events())

    def write_transcript_to_temp_file(self) -> Optional[str]:
        """Write transcript to a temporary text file and return the path.

        Returns None if there are no transcript events.
        """
        content = self.generate_transcript_text()
        if not content:
            return None

        temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        logger.debug(
            f"Writing transcript to temp file {temp_file.name} for workflow {self._workflow_run_id}"
        )
        temp_file.write(content)
        temp_file.close()

        logger.info(
            f"Successfully wrote {len(content)} chars of transcript to {temp_file.name}"
        )
        return temp_file.name

    @property
    def is_empty(self) -> bool:
        """Check if the buffer is empty."""
        return len(self._events) == 0
