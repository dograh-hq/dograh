import asyncio
import io
import wave
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
        self._turn_counter = 0
        self._current_node_id: Optional[str] = None
        self._current_node_name: Optional[str] = None
        self._user_speech_start_timestamp: Optional[str] = None
        self._user_speech_end_timestamp: Optional[str] = None
        self._bot_speech_start_timestamp: Optional[str] = None
        self._bot_speech_end_timestamp: Optional[str] = None

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

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).isoformat(timespec="milliseconds")

    def mark_user_started_speaking(self):
        """Record when the user started speaking for the current turn."""
        self._user_speech_start_timestamp = self._now_iso()
        self._user_speech_end_timestamp = None
        self._update_latest_payload_start_timestamp(
            RealtimeFeedbackType.USER_TRANSCRIPTION.value,
            self._user_speech_start_timestamp,
            require_final=True,
        )

    def mark_user_stopped_speaking(self):
        """Record when the user stopped speaking and update the latest user event."""
        self._user_speech_end_timestamp = self._now_iso()
        self._update_latest_payload_end_timestamp(
            RealtimeFeedbackType.USER_TRANSCRIPTION.value,
            self._user_speech_end_timestamp,
            require_final=True,
        )

    def mark_bot_started_speaking(self):
        """Record when the bot started speaking for the current assistant turn."""
        self._bot_speech_start_timestamp = self._now_iso()
        self._bot_speech_end_timestamp = None
        self._update_latest_payload_start_timestamp(
            RealtimeFeedbackType.BOT_TEXT.value,
            self._bot_speech_start_timestamp,
        )

    def mark_bot_stopped_speaking(self):
        """Record when the bot stopped speaking and update the latest bot event."""
        self._bot_speech_end_timestamp = self._now_iso()
        self._update_latest_payload_end_timestamp(
            RealtimeFeedbackType.BOT_TEXT.value,
            self._bot_speech_end_timestamp,
        )

    def _find_latest_open_payload(
        self, event_type: str, *, require_final: bool = False
    ) -> dict | None:
        for event in reversed(self._events):
            if event.get("type") != event_type:
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            if require_final and payload.get("final") is not True:
                continue
            if payload.get("end_timestamp"):
                continue
            return payload
        return None

    def _update_latest_payload_start_timestamp(
        self, event_type: str, start_timestamp: str, *, require_final: bool = False
    ):
        payload = self._find_latest_open_payload(
            event_type, require_final=require_final
        )
        if payload is not None:
            payload["timestamp"] = start_timestamp

    def _update_latest_payload_end_timestamp(
        self, event_type: str, end_timestamp: str, *, require_final: bool = False
    ):
        payload = self._find_latest_open_payload(
            event_type, require_final=require_final
        )
        if payload is not None:
            payload["end_timestamp"] = end_timestamp

    def _event_with_speech_timestamps(self, event: dict) -> dict:
        event_type = event.get("type")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return event

        payload_with_timestamps = dict(payload)
        if (
            event_type == RealtimeFeedbackType.USER_TRANSCRIPTION.value
            and payload.get("final") is True
        ):
            if self._user_speech_start_timestamp:
                payload_with_timestamps["timestamp"] = self._user_speech_start_timestamp
            if self._user_speech_end_timestamp:
                payload_with_timestamps["end_timestamp"] = self._user_speech_end_timestamp
        elif event_type == RealtimeFeedbackType.BOT_TEXT.value:
            if self._bot_speech_start_timestamp:
                payload_with_timestamps["timestamp"] = self._bot_speech_start_timestamp
            if self._bot_speech_end_timestamp:
                payload_with_timestamps["end_timestamp"] = self._bot_speech_end_timestamp

        if payload_with_timestamps == payload:
            return event
        return {**event, "payload": payload_with_timestamps}

    async def append(self, event: dict):
        """Append a feedback event to the buffer with timestamp and current node."""
        event = self._event_with_speech_timestamps(event)
        timestamped_event = stamp_realtime_feedback_event(
            event,
            timestamp=self._now_iso(),
            turn=self._turn_counter,
            node_id=self._current_node_id,
            node_name=self._current_node_name,
        )
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

    def _sorted_events(self) -> List[dict]:
        # Stable sort by the realtime (payload) timestamp when available, falling
        # back to the buffer-append timestamp. Python's sort is stable, so events
        # sharing a key retain their original insertion order — this keeps
        # consecutive bot-text chunks of a single turn contiguous.
        return sorted(self._events, key=realtime_feedback_event_sort_key)

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
