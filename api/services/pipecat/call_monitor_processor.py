"""One call-owned policy for response progress, user idleness and call duration."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from loguru import logger

from api.schemas.workflow_configurations import DEFAULT_MAX_CALL_DURATION_SECONDS
from api.services.pipecat.speech_playback import PlaybackOutcome
from pipecat.frames.frames import (
    BotSpeakingFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    FunctionCallCancelFrame,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    HeartbeatFrame,
    StartFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


@dataclass
class _ResponseWatch:
    """One owed response, including intervening tool generations."""

    source: FrameProcessor
    confirmed: bool
    tools: set[str] = field(default_factory=set)
    tool_deadline: float | None = None
    expired: bool = False
    awaiting_generation: bool = False
    awaiting_tool_response: bool = False
    latest_scope: str | None = None
    scopes: set[str] = field(default_factory=set)
    audible_scopes: set[str] = field(default_factory=set)
    finished_scopes: set[str] = field(default_factory=set)


class CallMonitorProcessor(FrameProcessor):
    """Own the call's conversational deadline and idle-reminder attempts.

    Speech frames, explicit requests, tools and playback report facts. This
    processor decides whether the call is waiting for its user or its agent.
    An audio pause does not finish a response: its last playback boundary must
    drain, with no tool continuation outstanding, before a fresh user-idle
    window can begin.

    Generation gets 35 seconds without delivered audio, tools get a fixed
    180-second budget, and the call's hard duration limit stays independent.
    Conversation monitoring starts on activation; the hard duration limit runs
    from pipeline startup. Timers run outside frame processing so a stalled
    agent cannot stall them.
    """

    def __init__(
        self,
        *,
        response_source: Callable[[], FrameProcessor | None],
        on_response_timeout: Callable[[FrameProcessor], None],
        on_user_idle: Callable[[int], Awaitable[None]],
        conversation_enabled: Callable[[], bool],
        response_timeout: float = 35,
        tool_timeout: float = 180,
        max_call_duration_seconds: int = DEFAULT_MAX_CALL_DURATION_SECONDS,
        max_duration_end_task_callback: Callable[[], Awaitable[None]] | None = None,
    ):
        super().__init__()
        self.response_timeout = response_timeout
        self.tool_timeout = tool_timeout
        self.user_idle_timeout = 0.0
        self._response_source = response_source
        self._on_response_timeout = on_response_timeout
        self._on_user_idle = on_user_idle
        self._conversation_enabled = conversation_enabled
        self._sources: dict[FrameProcessor, Callable[[], bool]] = {}
        self._response_watch: _ResponseWatch | None = None
        self._deadline: asyncio.TimerHandle | None = None
        self._deadline_kind: str | None = None
        self._revision = 0
        self._idle_task: asyncio.Task | None = None
        self._retry_count = 0
        self._waiting_for_user = False
        self._user_turn_active = False
        self._response_during_user_turn = False
        self._bot_speaking = False
        self.user_aggregator = None
        self._playbacks: set[str] = set()
        self._active = False
        self._suspended = False
        self._closed = False
        self._start_time = None
        self.max_call_duration_seconds = max_call_duration_seconds
        self._max_duration_end_task_callback = max_duration_end_task_callback
        self._end_task_frame_pushed = False

    def bind_user(self, aggregator, *, idle_timeout: float) -> None:
        """Observe accepted turns; Pipecat's own idle timer must be disabled."""
        if self.user_aggregator is not aggregator:
            self._unbind_user()
            self.user_aggregator = aggregator
            aggregator.add_event_handler("on_user_turn_started", self._user_started)
            aggregator.add_event_handler("on_user_turn_stopped", self._user_stopped)
            aggregator.user_turn_controller.add_event_handler(
                "on_user_turn_inference_triggered", self._user_inference_requested
            )
        self.user_idle_timeout = idle_timeout

    def _unbind_user(self) -> None:
        if self.user_aggregator is not None:
            self.user_aggregator.remove_event_handler(
                "on_user_turn_started", self._user_started
            )
            self.user_aggregator.remove_event_handler(
                "on_user_turn_stopped", self._user_stopped
            )
            self.user_aggregator.user_turn_controller.remove_event_handler(
                "on_user_turn_inference_triggered", self._user_inference_requested
            )
            self.user_aggregator = None

    def _user_started(self, _aggregator=None, *_args) -> None:
        if not self._user_turn_active:
            self._response_during_user_turn = False
        self._user_turn_active = True
        self._waiting_for_user = False
        self._retry_count = 0
        # Only an accepted interruption cancels an outstanding bot response.
        if self._deadline_kind == "idle":
            self._cancel_deadline()
        self._cancel_idle_action()

    def _user_stopped(self, _aggregator=None, *_args) -> None:
        was_active = self._user_turn_active
        self._user_turn_active = False
        # The aggregator event also covers strategies that disable speaking
        # frames. Only the first stop ends this turn; its duplicate cannot
        # create another response expectation after fast playback completes.
        if was_active:
            self._confirm_user_response()
        self._arm_idle()

    def _user_inference_requested(self, _controller, _strategy, speculation) -> None:
        # LLM-assisted turn detection requests a response before publishing
        # UserStoppedSpeakingFrame. Its explicit request event also bounds a
        # provider that fails before it can decide the turn has ended.
        if speculation is None:
            self._confirm_user_response()

    def _confirm_user_response(self) -> None:
        watch = self._response_watch
        if watch:
            if not watch.confirmed:
                watch.confirmed = True
                self._renew_deadline(watch)
            self._complete_response(watch)
        elif not self._response_during_user_turn:
            self.expect_response()

    @property
    def active(self) -> bool:
        return self._active

    def activate(self, *, waiting_for_user: bool = False) -> None:
        """Start once, before ordinary inference or after a supervised opening.

        Activation alone does not start an idle countdown. Supervision can
        explicitly hand over a listening call after finishing its opening.
        """
        if self._active or self._closed:
            return
        self._active = True
        self._waiting_for_user = self._waiting_for_user or waiting_for_user
        self._arm_idle()

    def suspend(self) -> None:
        """Pause conversation timing while a transfer owns call progress."""
        self._suspended = True
        self.cancel()

    def resume(self) -> None:
        self._suspended = False
        self._waiting_for_user = True
        self._arm_idle()

    def _idle_eligible(self) -> bool:
        user_active = self._user_turn_active or (
            self.user_aggregator is not None
            and self.user_aggregator.user_turn_controller.has_active_user_turn
        )
        return bool(
            self._active
            and not self._closed
            and not self._suspended
            and self._conversation_enabled()
            and self._waiting_for_user
            and not user_active
            and not self._bot_speaking
            and not self._response_watch
            and not self._playbacks
            and self.user_aggregator is not None
            and self.user_idle_timeout > 0
        )

    def _cancel_deadline(self) -> None:
        self._revision += 1
        if self._deadline:
            self._deadline.cancel()
        self._deadline = None
        self._deadline_kind = None

    def _cancel_idle_action(self) -> None:
        if self._idle_task and self._idle_task is not asyncio.current_task():
            self._idle_task.cancel()

    def _arm_idle(self) -> None:
        if not self._idle_eligible() or self._deadline_kind == "idle":
            return
        self._cancel_deadline()
        self._deadline_kind = "idle"
        self._deadline = asyncio.get_running_loop().call_later(
            self.user_idle_timeout, self._idle_expired, self._revision
        )

    def _idle_expired(self, revision: int) -> None:
        if revision != self._revision:
            return
        self._cancel_deadline()
        if not self._idle_eligible():
            return
        self._waiting_for_user = False
        self._retry_count += 1
        # Arm before queuing the reminder, including if the agent queue is stuck.
        self.expect_response()
        revision = self._revision
        attempt = self._retry_count

        async def notify():
            if (
                revision != self._revision
                or self._closed
                or not self._conversation_enabled()
            ):
                return
            try:
                await self._on_user_idle(attempt)
            except Exception:
                logger.exception("Could not request the call's idle reminder")
                # The already-armed response deadline still bounds this failure.

        self._idle_task = asyncio.create_task(notify(), name="call-user-idle")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self._start_time = time.monotonic()
        elif isinstance(frame, HeartbeatFrame):
            await self._check_call_duration()
        elif isinstance(frame, UserStartedSpeakingFrame):
            self._user_started()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._user_stopped()
        elif isinstance(frame, (BotStartedSpeakingFrame, BotSpeakingFrame)):
            self._bot_speaking = True
            if self._deadline_kind == "idle":
                self._cancel_deadline()
            # These precede the transport write. Delivered audio, observed at
            # output, renews the response deadline; a start alone cannot.
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            self._waiting_for_user = True
            # A transport pause is not completion of pending generation/tools.
            self._arm_idle()
        await self.push_frame(frame, direction)

    async def _check_call_duration(self):
        if self._start_time is None or self._end_task_frame_pushed:
            return
        if time.monotonic() - self._start_time <= self.max_call_duration_seconds:
            return
        if self._max_duration_end_task_callback:
            await self._max_duration_end_task_callback()
        self._end_task_frame_pushed = True

    async def cleanup(self):
        idle_task = self._idle_task
        self.close()
        if idle_task and idle_task is not asyncio.current_task():
            await asyncio.gather(idle_task, return_exceptions=True)
        await super().cleanup()

    @property
    def pending_response(self) -> bool:
        return self._response_watch is not None

    def bind_source(self, source, *, enabled: Callable[[], bool]) -> None:
        """Observe tool execution, using the engine's visit gate."""
        if self._closed or not isinstance(source, FrameProcessor):
            return
        if source not in self._sources:
            source.add_event_handler("on_before_push_frame", self._watch_tool)
        self._sources[source] = enabled

    def _source_enabled(self, source: FrameProcessor | None) -> bool:
        if not isinstance(source, FrameProcessor):
            return False
        enabled = self._sources.get(source)
        return bool(
            self._active
            and not self._closed
            and not self._suspended
            and enabled
            and enabled()
        )

    def expect_response(self, source: FrameProcessor | None = None) -> None:
        """An accepted user turn or explicit engine request needs an answer.

        Call before dispatch so a stuck agent queue is bounded. Context frames
        deliberately have no meaning here: realtime providers also use them
        to synchronize transcripts without requesting another generation.
        """
        source = source if source is not None else self._response_source()
        if not self._source_enabled(source):
            return
        watch = self._start_response(source, confirmed=True)
        watch.awaiting_generation = True

    def _start_response(
        self, source: FrameProcessor, *, confirmed: bool
    ) -> _ResponseWatch:
        self._waiting_for_user = False
        if self._deadline_kind == "idle":
            self._cancel_deadline()
        watch = self._response_watch
        if watch is None or watch.source is not source:
            self.cancel()
            watch = _ResponseWatch(source=source, confirmed=confirmed)
            self._response_watch = watch
            self._renew_deadline(watch)
        elif confirmed and not watch.confirmed:
            watch.confirmed = True
            self._renew_deadline(watch)
        # Repeated requests/retries without delivered audio do not buy time.
        return watch

    def cancel(self) -> None:
        """Invalidate the current response on interruption or handoff."""
        self._cancel_deadline()
        self._cancel_idle_action()
        self._response_watch = None
        self._waiting_for_user = False

    def close(self) -> None:
        """Release the deadline and subscriptions at call closure."""
        self._closed = True
        self._active = False
        self.cancel()
        self._unbind_user()
        self._playbacks.clear()
        for source in self._sources:
            source.remove_event_handler("on_before_push_frame", self._watch_tool)
        self._sources.clear()

    def response_timed_out(self, source: FrameProcessor) -> bool:
        watch = self._response_watch
        return bool(watch and watch.source is source and watch.expired)

    def _renew_deadline(self, watch: _ResponseWatch) -> None:
        if self._closed or self._suspended or not watch.confirmed or watch.expired:
            return
        self._cancel_deadline()
        loop = asyncio.get_running_loop()
        deadline = watch.tool_deadline or (loop.time() + self.response_timeout)
        self._deadline_kind = "response"
        self._deadline = loop.call_at(
            deadline, self._response_expired, watch, self._revision
        )

    def _response_expired(self, watch: _ResponseWatch, revision: int) -> None:
        if self._response_watch is not watch or revision != self._revision:
            return
        watch.expired = True
        self._on_response_timeout(watch.source)

    def _watch_tool(self, source: FrameProcessor, frame: Frame) -> None:
        if not self._source_enabled(source):
            return
        if isinstance(frame, FunctionCallsStartedFrame):
            self._start_response(source, confirmed=not self._user_turn_active)
        watch = self._response_watch
        if not watch or watch.source is not source:
            return
        if isinstance(frame, FunctionCallsStartedFrame):
            new_ids = {call.tool_call_id for call in frame.function_calls} - watch.tools
            if new_ids:
                if not watch.tools:
                    watch.tool_deadline = (
                        asyncio.get_running_loop().time() + self.tool_timeout
                    )
                watch.tools.update(new_ids)
                watch.awaiting_tool_response = True
                self._renew_deadline(watch)
        elif isinstance(frame, (FunctionCallResultFrame, FunctionCallCancelFrame)):
            if (
                isinstance(frame, FunctionCallResultFrame)
                and frame.properties
                and not frame.properties.is_final
            ):
                return
            if frame.tool_call_id in watch.tools:
                watch.tools.remove(frame.tool_call_id)
                if not watch.tools:
                    watch.tool_deadline = None
                    run_llm = frame.run_llm
                    if (
                        isinstance(frame, FunctionCallResultFrame)
                        and frame.properties
                        and frame.properties.run_llm is not None
                    ):
                        run_llm = frame.properties.run_llm
                    # Normal results still owe a spoken follow-up, even if a
                    # preamble has drained. Explicitly suppressed follow-ups
                    # have no future generation-start event to clear this flag.
                    watch.awaiting_tool_response = run_llm is not False
                    self._renew_deadline(watch)
                    self._complete_response(watch)

    def on_response_expected(self, source: FrameProcessor) -> None:
        self.expect_response(source)

    def on_response_started(self, source: FrameProcessor, speech_id: str) -> None:
        if not self._source_enabled(source):
            return
        if self._user_turn_active:
            self._response_during_user_turn = True
        watch = self._start_response(source, confirmed=not self._user_turn_active)
        watch.scopes.add(speech_id)
        watch.latest_scope = speech_id
        watch.awaiting_generation = False
        if not watch.tools:
            watch.awaiting_tool_response = False

    def on_output(self, speech_id: str) -> None:
        watch = self._response_watch
        if watch and speech_id in watch.scopes:
            watch.audible_scopes.add(speech_id)
            self._renew_deadline(watch)

    def on_playback_expected(self, speech_id: str) -> None:
        """Include direct speech/recordings that bypass generation entirely."""
        if self._closed:
            return
        self._playbacks.add(speech_id)
        self._waiting_for_user = False
        if self._deadline_kind == "idle":
            self._cancel_deadline()
        source = self._response_source()
        if self._source_enabled(source):
            watch = self._start_response(source, confirmed=True)
            watch.scopes.add(speech_id)
            if watch.latest_scope is None and not watch.awaiting_generation:
                # Direct TTS/recordings have no LLM start frame. Their own
                # playback boundary completes this explicit response request.
                watch.latest_scope = speech_id

    def on_speech_finished(self, speech_id: str, outcome: PlaybackOutcome) -> None:
        if speech_id not in self._playbacks:
            return
        self._playbacks.remove(speech_id)
        watch = self._response_watch
        if (
            outcome is PlaybackOutcome.SKIPPED
            and watch
            and watch.latest_scope == speech_id
            and not watch.awaiting_generation
            and not watch.tools
        ):
            self.cancel()
            self._waiting_for_user = True
        if outcome is PlaybackOutcome.PLAYED:
            self._waiting_for_user = True
        self._arm_idle()

    def on_playback_finished(self, speech_id: str) -> None:
        watch = self._response_watch
        if not watch or speech_id not in watch.scopes:
            return
        watch.finished_scopes.add(speech_id)
        self._complete_response(watch)

    def _complete_response(self, watch: _ResponseWatch) -> None:
        if (
            watch.confirmed
            and watch.latest_scope in watch.audible_scopes
            and watch.latest_scope in watch.finished_scopes
            and not watch.tools
            and not watch.awaiting_generation
            and not watch.awaiting_tool_response
        ):
            self.cancel()
            self._waiting_for_user = True
            self._arm_idle()
        # Empty and tool-only generations keep the deadline until their
        # continuation actually reaches output.

    def on_playback_cancelled(self, outcome: PlaybackOutcome) -> None:
        self._bot_speaking = False
        self._response_during_user_turn = False
        self._playbacks.clear()
        if outcome is PlaybackOutcome.CLOSED:
            self.close()
        else:
            self.cancel()
