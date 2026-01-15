import asyncio
import re
import tempfile
import wave
from datetime import UTC, datetime
from typing import List

from loguru import logger


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


class InMemoryTranscriptBuffer:
    """Buffer transcript data in memory during a call, then write to temp file on disconnect."""

    # Compiled regex to identify user speech lines, e.g.
    # [2025-06-29T12:34:56.789+00:00] user: hello
    _USER_SPEECH_RE: re.Pattern[str] = re.compile(
        r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+\d{2}:\d{2}\] user: .+"
    )

    def __init__(self, workflow_run_id: int):
        self._workflow_run_id = workflow_run_id
        self._lines: List[str] = []
        self._lock = asyncio.Lock()

    async def append(self, transcript: str):
        """Append transcript text to the buffer."""
        async with self._lock:
            self._lines.append(transcript)
            logger.trace(
                f"Appended transcript line to buffer for workflow {self._workflow_run_id}"
            )

    async def write_to_temp_file(self) -> str:
        """Write transcript to a temporary text file and return the path."""
        async with self._lock:
            temp_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False
            )
            logger.debug(
                f"Writing transcript buffer to temp file {temp_file.name} for workflow {self._workflow_run_id}"
            )

            content = "".join(self._lines)
            temp_file.write(content)
            temp_file.close()

            logger.info(
                f"Successfully wrote {len(content)} chars of transcript to {temp_file.name}"
            )
            return temp_file.name

    @property
    def is_empty(self) -> bool:
        """Check if the buffer is empty."""
        return len(self._lines) == 0

    def contains_user_speech(self) -> bool:
        """Return True if any buffered transcript line matches the user speech pattern."""
        for line in self._lines:
            if self._USER_SPEECH_RE.match(line):
                return True
        return False


class InMemoryLogsBuffer:
    """Buffer real-time feedback events in memory during a call, then save to workflow run logs."""

    def __init__(self, workflow_run_id: int):
        self._workflow_run_id = workflow_run_id
        self._events: List[dict] = []
        self._turn_counter = 0

    async def append(self, event: dict):
        """Append a feedback event to the buffer with timestamp."""
        # Add timestamp and turn tracking
        timestamped_event = {
            **event,
            "timestamp": datetime.now(UTC).isoformat(),
            "turn": self._turn_counter,
        }
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

    def get_events(self) -> List[dict]:
        """Get all events for final storage."""
        return self._events

    @property
    def is_empty(self) -> bool:
        """Check if the buffer is empty."""
        return len(self._events) == 0
