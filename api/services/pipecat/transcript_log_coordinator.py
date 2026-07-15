"""Turn-aware coordination for immutable persisted transcript events.

The transcript text, speech timing, and logical turn lifecycle are produced by
different parts of the pipeline and can arrive in either order. This module is
the single place where those facts are joined. It emits a transcript event only
after the owning logical turn has ended (or during a final flush), and never
mutates an event after it has been appended to the logs buffer.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from api.services.pipecat.realtime_feedback_events import (
    build_bot_text_event,
    build_user_transcription_event,
)

if TYPE_CHECKING:
    from api.services.pipecat.in_memory_buffers import InMemoryLogsBuffer
    from pipecat.observers.turn_tracking_observer import TurnTrackingObserver


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass
class _TranscriptSide:
    text: str | None = None
    transcript_timestamp: str | None = None
    event_timestamp: str | None = None
    speech_start_timestamp: str | None = None
    speech_end_timestamp: str | None = None
    speaking: bool = False
    emitted: bool = False
    node_id: str | None = None
    node_name: str | None = None


@dataclass
class _TurnTranscriptState:
    turn_id: int
    ended: bool = False
    interrupted: bool = False
    user: _TranscriptSide = field(default_factory=_TranscriptSide)
    assistant: _TranscriptSide = field(default_factory=_TranscriptSide)


class TranscriptLogCoordinator:
    """Join turn, transcript, and speech facts before appending log events."""

    def __init__(self, logs_buffer: "InMemoryLogsBuffer"):
        self._logs_buffer = logs_buffer
        self._states: dict[int, _TurnTranscriptState] = {}
        self._active_turn_id: int | None = None
        self._lock = asyncio.Lock()

    def attach_turn_tracking_observer(self, observer: "TurnTrackingObserver") -> None:
        """Subscribe to the canonical turn owner's correlated lifecycle events."""

        @observer.event_handler("on_turn_started")
        async def on_turn_started(_observer, turn_number: int):
            await self.record_turn_started(turn_number)

        @observer.event_handler("on_turn_ended")
        async def on_turn_ended(
            _observer,
            turn_number: int,
            _duration: float,
            was_interrupted: bool,
        ):
            await self.record_turn_ended(turn_number, interrupted=was_interrupted)

        @observer.event_handler("on_user_speech_started_for_turn")
        async def on_user_speech_started_for_turn(_observer, turn_number: int, _data):
            callback_timestamp = _now_iso()
            await self.record_user_started_speaking(turn_number, callback_timestamp)

        @observer.event_handler("on_user_speech_stopped_for_turn")
        async def on_user_speech_stopped_for_turn(_observer, turn_number: int, _data):
            callback_timestamp = _now_iso()
            await self.record_user_stopped_speaking(turn_number, callback_timestamp)

        @observer.event_handler("on_bot_started_speaking")
        async def on_bot_started_speaking(_observer, turn_number: int, _data):
            await self.record_bot_started_speaking(turn_number)

        @observer.event_handler("on_bot_stopped_speaking")
        async def on_bot_stopped_speaking(_observer, turn_number: int, _data):
            await self.record_bot_stopped_speaking(turn_number)

    def _state(self, turn_id: int) -> _TurnTranscriptState:
        state = self._states.get(turn_id)
        if state is None:
            state = _TurnTranscriptState(turn_id=turn_id)
            self._states[turn_id] = state
        return state

    async def record_turn_started(self, turn_id: int) -> None:
        async with self._lock:
            self._state(turn_id)
            if self._active_turn_id is None or turn_id >= self._active_turn_id:
                self._active_turn_id = turn_id
                self._logs_buffer.set_current_turn(turn_id)

    async def record_turn_ended(self, turn_id: int, *, interrupted: bool) -> None:
        async with self._lock:
            state = self._state(turn_id)
            state.ended = True
            state.interrupted = interrupted
            if self._active_turn_id == turn_id:
                self._active_turn_id = None
            await self._emit_ready_sides(state)

    async def record_user_started_speaking(
        self, turn_id: int, timestamp: str | None = None
    ) -> None:
        async with self._lock:
            state = self._state(turn_id)
            side = state.user
            previous_start = side.speech_start_timestamp
            candidate_start = timestamp or _now_iso()
            side.speech_start_timestamp = (
                min(previous_start, candidate_start)
                if previous_start is not None
                else candidate_start
            )
            side.speaking = True

    async def record_user_stopped_speaking(
        self, turn_id: int, timestamp: str | None = None
    ) -> None:
        async with self._lock:
            side = self._state(turn_id).user
            previous_end = side.speech_end_timestamp
            candidate_end = timestamp or _now_iso()
            side.speech_end_timestamp = (
                max(previous_end, candidate_end)
                if previous_end is not None
                else candidate_end
            )
            side.speaking = False

    async def record_bot_started_speaking(
        self, turn_id: int, timestamp: str | None = None
    ) -> None:
        async with self._lock:
            side = self._state(turn_id).assistant
            if side.speech_start_timestamp is None:
                side.speech_start_timestamp = timestamp or _now_iso()
            side.speaking = True

    async def record_bot_stopped_speaking(
        self, turn_id: int, timestamp: str | None = None
    ) -> None:
        async with self._lock:
            state = self._state(turn_id)
            side = state.assistant
            side.speech_end_timestamp = timestamp or _now_iso()
            side.speaking = False
            await self._emit_ready_sides(state)

    async def record_user_transcript(
        self,
        *,
        text: str,
        timestamp: str | None,
        end_timestamp: str | None = None,
        event_timestamp: str | None = None,
    ) -> None:
        async with self._lock:
            state = self._select_user_turn()
            side = state.user
            first_text = side.text is None
            side.text = text if first_text else f"{side.text}\n{text}"
            if first_text:
                side.transcript_timestamp = timestamp
                self._capture_node(side)
            side.event_timestamp = event_timestamp or _now_iso()
            if end_timestamp and not side.speech_end_timestamp:
                side.speech_end_timestamp = end_timestamp
            await self._emit_ready_sides(state)

    async def record_assistant_transcript(
        self,
        *,
        text: str,
        timestamp: str | None,
        end_timestamp: str | None = None,
        event_timestamp: str | None = None,
    ) -> None:
        async with self._lock:
            state = self._select_assistant_turn()
            side = state.assistant
            first_text = side.text is None
            side.text = text if first_text else f"{side.text}\n{text}"
            if first_text:
                side.transcript_timestamp = timestamp
                self._capture_node(side)
            side.event_timestamp = event_timestamp or _now_iso()
            if end_timestamp and not side.speech_end_timestamp:
                side.speech_end_timestamp = end_timestamp
            await self._emit_ready_sides(state)

    def _select_user_turn(self) -> _TurnTranscriptState:
        if self._active_turn_id is not None:
            active = self._state(self._active_turn_id)
            if active.user.text is None:
                return active
        candidates = [
            state
            for state in self._states.values()
            if state.user.speech_start_timestamp and state.user.text is None
        ]
        if candidates:
            return min(candidates, key=lambda state: state.turn_id)
        if self._states:
            return max(self._states.values(), key=lambda state: state.turn_id)
        return self._state(1)

    def _select_assistant_turn(self) -> _TurnTranscriptState:
        candidates = [
            state
            for state in self._states.values()
            if state.assistant.speech_start_timestamp and state.assistant.text is None
        ]
        interrupted = [
            state for state in candidates if state.ended and state.interrupted
        ]
        if interrupted:
            return min(interrupted, key=lambda state: state.turn_id)
        if self._active_turn_id is not None:
            active = self._state(self._active_turn_id)
            if active.assistant.text is None:
                return active
        if candidates:
            return min(candidates, key=lambda state: state.turn_id)
        if self._states:
            return max(self._states.values(), key=lambda state: state.turn_id)
        return self._state(1)

    def _capture_node(self, side: _TranscriptSide) -> None:
        side.node_id = self._logs_buffer.current_node_id
        side.node_name = self._logs_buffer.current_node_name

    async def _emit_ready_sides(self, state: _TurnTranscriptState) -> None:
        if not state.ended:
            return
        await self._emit_user(state)
        if not state.assistant.speaking:
            await self._emit_assistant(state)

    async def _emit_user(self, state: _TurnTranscriptState) -> None:
        side = state.user
        if side.emitted or not side.text:
            return
        event = build_user_transcription_event(
            text=side.text,
            final=True,
            timestamp=side.speech_start_timestamp or side.transcript_timestamp,
            end_timestamp=side.speech_end_timestamp,
        )
        await self._append(state, side, event)

    async def _emit_assistant(self, state: _TurnTranscriptState) -> None:
        side = state.assistant
        if side.emitted or not side.text:
            return
        event = build_bot_text_event(
            text=side.text,
            timestamp=side.speech_start_timestamp or side.transcript_timestamp,
            end_timestamp=side.speech_end_timestamp,
        )
        await self._append(state, side, event)

    async def _append(
        self, state: _TurnTranscriptState, side: _TranscriptSide, event: dict
    ) -> None:
        await self._logs_buffer.append(
            event,
            timestamp=side.event_timestamp,
            turn=state.turn_id,
            node_id=side.node_id,
            node_name=side.node_name,
            use_current_node=False,
        )
        side.emitted = True

    async def flush(self) -> None:
        """Emit any remaining transcript text without inventing missing timing."""
        async with self._lock:
            for state in sorted(self._states.values(), key=lambda item: item.turn_id):
                state.ended = True
                state.user.speaking = False
                state.assistant.speaking = False
                await self._emit_ready_sides(state)
