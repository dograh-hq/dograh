"""Observer that turns pipeline frames and events into diagnostic rows.

Why this exists: in production every signal that distinguishes the possible
causes of a silent turn — user muted until the first bot completion, a node
transition function that never returns, an external-turn stop strategy waiting
for text that never arrives, a refusal filtered out of the response — is a
``logger.debug`` or ``trace`` line, and production runs at ``LOG_LEVEL=INFO``.
Nothing is left to correlate after the call. This observer records those facts
as structured events instead, at a volume that is a few hundred rows per call.

Two watchdogs turn "nothing happened" into an actual event, which is the part
logs cannot do: :attr:`FUNCTION_CALL_HUNG` for a function call without a
result, and :attr:`BOT_SILENT_AFTER_USER_TURN` for a released user turn the bot
never answers — with a snapshot of the mute state, the in-flight function
calls, the open LLM response and the dropped refusals, so the cause is readable
from the row itself.
"""

import asyncio
import functools
import statistics
import time
from collections import OrderedDict
from typing import Any

from loguru import logger
from pipecat.frames.frames import (
    BotSpeakingFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    FunctionCallCancelFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    TranscriptionFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    UserSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.base_output import BaseOutputTransport

from api.services.observability.call_events import events as ev
from api.services.observability.call_events.events import CallEvent
from api.services.observability.call_events.metrics import ttfb_kind

DEFAULT_HUNG_AFTER_S = 15.0
DEFAULT_SILENT_AFTER_S = 8.0

# A frame is pushed between every pair of processors it traverses, so each one
# is observed many times. Bounded because a call must not accumulate ids.
_SEEN_FRAMES_MAX = 2048

# Audio frames are the bulk of the traffic and carry nothing diagnosable.
_IGNORED_FRAMES = (
    InputAudioRawFrame,
    OutputAudioRawFrame,
    BotSpeakingFrame,
    UserSpeakingFrame,
)


def _never_raises(method):
    """Keep a diagnostics bug from surfacing in the call being measured."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception as e:
            self._log_internal_error(method.__name__, e)
        return None

    return wrapper


class CallEventRecorder:
    """Collect the call event contract through existing observation hooks."""

    def __init__(
        self,
        *,
        sink: Any,
        run_id: int | None,
        org_id: int | None,
        workflow_id: int | None,
        engine: Any = None,
        hung_after_s: float = DEFAULT_HUNG_AFTER_S,
        silent_after_s: float = DEFAULT_SILENT_AFTER_S,
    ):
        """
        Args:
            sink: Object with an ``emit(CallEvent)`` method.
            run_id: Workflow run id, the join key for every row.
            org_id: Organization id.
            workflow_id: Workflow id.
            engine: ``PipecatEngine``, read duck-typed for the mute snapshot
                and the current node. Optional.
            hung_after_s: Delay before a function call is reported hung.
            silent_after_s: Delay before an unanswered user turn is reported.
        """
        self._sink = sink
        self._run_id = run_id
        self._org_id = org_id
        self._workflow_id = workflow_id
        self._engine = engine
        self._hung_after_s = hung_after_s
        self._silent_after_s = silent_after_s

        self._started_at = time.monotonic()
        self._seen_frames: OrderedDict[int, None] = OrderedDict()
        self._internal_error_logged = False

        # Turn state
        self._turn = 0
        self._interim_count = 0
        self._final_count = 0
        self._final_chars = 0
        self._llm_open = False
        self._llm_text_chars = 0
        self._llm_chars_since_turn = 0
        self._refusals_since_turn = 0
        self._tts_started_since_turn = False
        self._bot_spoke_since_user_turn = False
        self._last_user_turn_stopped_at: float | None = None
        self._user_speech_seen = False
        # MuteUntilFirstBotCompleteUserMuteStrategy holds the user muted until
        # the first BotStoppedSpeakingFrame of the whole call.
        self._first_bot_completed = False

        # Mute state
        self._muted = False
        self._mute_started_at: float | None = None

        # In-flight function calls: tool_call_id -> (name, started monotonic)
        self._function_calls: dict[str, tuple[str, float]] = {}

        # bot_speaking_start carries a latency measured off the frames, the
        # same way pipecat's own observer does it, because that event is
        # emitted even when the latency observer is not attached.
        self._user_stopped_at: float | None = None
        self._last_e2e_ms: float | None = None
        # latency_breakdown instead reports pipecat's own measurement, handed
        # over by on_latency_measured just before the breakdown arrives.
        self._pending_e2e_ms: float | None = None
        self._e2e_samples: list[float] = []
        self._user_turn_samples: list[float] = []

        # Running totals for the call_ended summary
        self._mute_ms_total = 0.0
        self._idle_events = 0
        self._empty_finals = 0
        self._hung_function_calls = 0
        self._silent_after_turn = 0
        self._refusals_dropped = 0
        self._llm_empty_responses = 0
        self._stop_timeouts = 0
        self._interruptions = 0

        self._silent_task: asyncio.Task | None = None
        self._hung_tasks: dict[str, asyncio.Task] = {}
        self._call_ended = False
        self._source_node = None
        self._watchdog_tasks: set[asyncio.Task] = set()

    # ---------------------------------------------------------------- state

    @property
    def turn(self) -> int:
        """Current turn number, shared by every event of this call."""
        return self._turn

    @property
    def mute_ms_total(self) -> float:
        """Total time the user has been muted so far, in milliseconds."""
        return self._mute_ms_total

    @property
    def hung_function_calls(self) -> int:
        """Number of function calls that passed the hung watchdog."""
        return self._hung_function_calls

    def set_turn(self, turn: int) -> None:
        """Set the turn number stamped on subsequent events."""
        self._turn = turn

    # --------------------------------------------------------------- frames

    async def on_push_frame(self, data: FramePushed):
        """Record the frames that say something about turn taking."""
        frame = data.frame
        if self._call_ended or isinstance(frame, _IGNORED_FRAMES):
            return
        try:
            from api.services.pipecat.agent_bridge import AgentWorker

            worker = getattr(data.source, "pipeline_worker", None)
            if isinstance(worker, AgentWorker):
                agent = getattr(self._engine, "active_agent", None)
                if agent is None or worker.name != agent.visit_id:
                    # Late conversation output was not delivered to the caller.
                    # Retain errors and completion of tools already observed.
                    if not isinstance(
                        frame,
                        (ErrorFrame, FunctionCallResultFrame, FunctionCallCancelFrame),
                    ):
                        return
                    agent = next(
                        (
                            a
                            for a in getattr(self._engine, "_retired_agents", [])
                            if a.visit_id == worker.name
                        ),
                        None,
                    )
                node = getattr(agent, "current_node", None)
                self._source_node = (
                    getattr(node, "id", None),
                    getattr(node, "name", None),
                )
            await self._handle_frame(data)
        except Exception as e:
            self._log_internal_error("on_push_frame", e)
        finally:
            self._source_node = None

    async def _handle_frame(self, data: FramePushed):
        frame = data.frame
        if isinstance(frame, ErrorFrame):
            self.on_pipeline_error(frame)
            return
        if data.direction != FrameDirection.DOWNSTREAM:
            upstream_transcript = (
                isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame))
                and frame.broadcast_sibling_id is None
            )
            if not isinstance(frame, ErrorFrame) and not upstream_transcript:
                return

        # Bot speaking is only meaningful once the output transport has it:
        # earlier copies are the same frame travelling through the pipeline.
        # The frame is left unmarked so the transport copy is still handled.
        is_bot_speaking = isinstance(
            frame, (BotStartedSpeakingFrame, BotStoppedSpeakingFrame)
        )
        if is_bot_speaking and not isinstance(data.source, BaseOutputTransport):
            return

        if self._seen(frame):
            return

        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._user_speech_seen = True
            self._emit(ev.USER_SPEAKING_START)
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            # Subtract the silence the VAD had to observe before deciding, so
            # the latency is measured from the moment the user actually stopped.
            self._user_stopped_at = getattr(frame, "timestamp", time.time()) - getattr(
                frame, "stop_secs", 0.0
            )
            self._emit(ev.USER_SPEAKING_STOP)
        elif isinstance(frame, InterimTranscriptionFrame):
            self._interim_count += 1
        elif isinstance(frame, TranscriptionFrame):
            text = frame.text or ""
            self._final_count += 1
            self._final_chars += len(text)
            empty = not text.strip()
            if empty:
                self._empty_finals += 1
            self._emit(
                ev.TRANSCRIPT_FINAL,
                detail={
                    "chars": len(text),
                    "empty": empty,
                    "finalized": bool(getattr(frame, "finalized", False)),
                },
            )
        elif isinstance(frame, LLMFullResponseStartFrame):
            self._llm_open = True
            self._llm_text_chars = 0
        elif isinstance(frame, LLMTextFrame):
            chars = len(frame.text or "")
            self._llm_text_chars += chars
            self._llm_chars_since_turn += chars
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._llm_open = False
            if self._llm_text_chars == 0:
                self._llm_empty_responses += 1
                self._emit(ev.LLM_RESPONSE_EMPTY, severity=ev.SEVERITY_WARN)
        elif isinstance(frame, FunctionCallInProgressFrame):
            self._function_call_started(frame)
        elif isinstance(frame, FunctionCallResultFrame):
            self._function_call_finished(frame, cancelled=False)
        elif isinstance(frame, FunctionCallCancelFrame):
            self._function_call_finished(frame, cancelled=True)
        elif isinstance(frame, TTSStartedFrame):
            self._tts_started_since_turn = True
            self._emit(ev.TTS_STARTED)
        elif isinstance(frame, TTSStoppedFrame):
            self._emit(ev.TTS_STOPPED)
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_started_speaking()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._first_bot_completed = True
            self._emit(ev.BOT_SPEAKING_STOP)
        elif isinstance(frame, InterruptionFrame):
            self._interruptions += 1
            self._emit(ev.INTERRUPTION)

    def _seen(self, frame) -> bool:
        """True when this frame was already accounted for."""
        key = frame.id
        if key in self._seen_frames:
            return True
        self._seen_frames[key] = None
        if len(self._seen_frames) > _SEEN_FRAMES_MAX:
            self._seen_frames.popitem(last=False)
        return False

    def _bot_started_speaking(self) -> None:
        self._bot_spoke_since_user_turn = True
        self._cancel_silence_watchdog()
        # Cleared first: a bot turn with no user turn in front of it (the
        # greeting) has no end-to-end latency, and reporting the previous
        # turn's number there would be worse than reporting nothing.
        self._last_e2e_ms = None
        if self._user_stopped_at is not None:
            self._last_e2e_ms = (time.time() - self._user_stopped_at) * 1000.0
            self._e2e_samples.append(self._last_e2e_ms)
            self._user_stopped_at = None
        self._emit(ev.BOT_SPEAKING_START, value_ms=self._last_e2e_ms)

    def _function_call_started(self, frame) -> None:
        tool_call_id = getattr(frame, "tool_call_id", None) or ""
        name = getattr(frame, "function_name", None) or "unknown"
        self._function_calls[tool_call_id] = (name, time.monotonic())
        self._emit(
            ev.FUNCTION_CALL_STARTED,
            detail={"name": name, "tool_call_id": tool_call_id},
        )
        task = self._spawn(
            self._hung_watchdog(tool_call_id), f"function-call-hung-{tool_call_id}"
        )
        if task is not None:
            self._hung_tasks[tool_call_id] = task

    def _function_call_finished(self, frame, *, cancelled: bool) -> None:
        tool_call_id = getattr(frame, "tool_call_id", None) or ""
        entry = self._function_calls.pop(tool_call_id, None)
        self._cancel_task(self._hung_tasks.pop(tool_call_id, None))
        if entry is None:
            return
        name, started = entry
        self._emit(
            ev.FUNCTION_CALL_ENDED,
            value_ms=(time.monotonic() - started) * 1000.0,
            detail={
                "name": name,
                "tool_call_id": tool_call_id,
                "cancelled": cancelled,
            },
        )

    # ------------------------------------------------------- pipecat events

    @_never_raises
    def on_user_turn_started(self, strategy: Any) -> None:
        """A user turn opened: everything per-turn restarts from here."""
        self.set_turn(self._turn + 1)
        self._interim_count = 0
        self._final_count = 0
        self._final_chars = 0
        self._llm_chars_since_turn = 0
        self._refusals_since_turn = 0
        self._tts_started_since_turn = False
        self._bot_spoke_since_user_turn = False
        self._cancel_silence_watchdog()
        self._emit(ev.USER_TURN_STARTED, detail={"strategy": _class_name(strategy)})

    @_never_raises
    def on_user_turn_stopped(self, strategy: Any, message: Any = None) -> None:
        """The turn was released to the LLM: arm the silence watchdog."""
        self._last_user_turn_stopped_at = time.monotonic()
        self._emit(
            ev.USER_TURN_STOPPED,
            detail={
                "strategy": _class_name(strategy),
                "interim_count": self._interim_count,
                "final_count": self._final_count,
                "final_chars": self._final_chars,
            },
        )
        self._cancel_silence_watchdog()
        self._silent_task = self._spawn(
            self._silence_watchdog(self._turn), f"bot-silent-turn-{self._turn}"
        )

    @_never_raises
    def on_user_turn_stop_timeout(self) -> None:
        """No stop strategy fired; the aggregator released the turn on timeout."""
        self._stop_timeouts += 1
        self._emit(ev.USER_TURN_STOP_TIMEOUT, severity=ev.SEVERITY_WARN)

    @_never_raises
    def on_user_turn_idle(self, *, retry: int, total: int) -> None:
        """The idle controller prompted (or gave up on) a quiet user."""
        self._idle_events += 1
        self._emit(
            ev.USER_TURN_IDLE,
            severity=ev.SEVERITY_WARN,
            detail={"retry": retry, "total": total},
        )

    @_never_raises
    def on_mute_started(self) -> None:
        """The user was muted: transcription frames are dropped until unmute."""
        self._muted = True
        self._mute_started_at = time.monotonic()
        self._emit(ev.MUTE_STARTED, detail=self._mute_snapshot())

    @_never_raises
    def on_mute_stopped(self) -> None:
        """The user was unmuted; the duration is the interesting part."""
        self._muted = False
        duration_ms = None
        if self._mute_started_at is not None:
            duration_ms = (time.monotonic() - self._mute_started_at) * 1000.0
            self._mute_ms_total += duration_ms
            self._mute_started_at = None
        self._emit(ev.MUTE_STOPPED, value_ms=duration_ms, detail=self._mute_snapshot())

    @_never_raises
    def on_refusal_dropped(self, text: str) -> None:
        """The refusal filter suppressed a sentence before it reached TTS."""
        self._refusals_dropped += 1
        self._refusals_since_turn += 1
        self._emit(
            ev.REFUSAL_DROPPED,
            severity=ev.SEVERITY_WARN,
            detail={"chars": len(text or "")},
        )

    @_never_raises
    def on_latency_measured(self, latency_seconds: float) -> None:
        """Pipecat's user→bot latency for the cycle it is about to break down.

        Pipecat emits this immediately before ``on_latency_breakdown`` on the
        same object, and async handlers run as tasks created in that order, so
        the value waiting here belongs to the next breakdown.
        """
        self._pending_e2e_ms = _to_ms(latency_seconds)

    @_never_raises
    def on_latency_breakdown(self, breakdown: Any) -> None:
        """Per-component latency for one user→bot cycle."""
        user_turn_ms = _to_ms(getattr(breakdown, "user_turn_secs", None))
        if user_turn_ms is not None:
            self._user_turn_samples.append(user_turn_ms)

        # Consumed, not reused: a breakdown emitted for the first-bot-speech
        # window alone has no user→bot cycle behind it, and reporting the
        # previous turn's number there would be worse than reporting nothing.
        e2e_ms, self._pending_e2e_ms = self._pending_e2e_ms, None

        detail: dict[str, Any] = {
            # Turn release to first TTS audio. `e2e_ms` stays alongside it as
            # pipecat's raw measurement, which covers a different interval.
            "turn_to_audio_ms": _round_ms(_turn_to_audio_ms(breakdown)),
            "e2e_ms": _round_ms(e2e_ms),
            "user_turn_ms": _round_ms(user_turn_ms),
            "stt_ttfb_ms": None,
            "llm_ttfb_ms": None,
            "tts_ttfb_ms": None,
        }
        # More than one measurement per component in a cycle is unusual (a
        # reconnect, or the first-bot-speech window); keep the worst one.
        for metrics in getattr(breakdown, "ttfb", None) or []:
            kind = ttfb_kind(getattr(metrics, "processor", None))
            if not kind:
                continue
            key = f"{kind}_ttfb_ms"
            value = _round_ms(_to_ms(getattr(metrics, "duration_secs", None)))
            if value is not None and (detail[key] is None or value > detail[key]):
                detail[key] = value

        aggregation = getattr(breakdown, "text_aggregation", None)
        detail["text_aggregation_ms"] = _round_ms(
            _to_ms(getattr(aggregation, "duration_secs", None))
        )
        function_calls = getattr(breakdown, "function_calls", None) or []
        detail["function_calls_ms"] = _round_ms(
            sum(
                _to_ms(getattr(fc, "duration_secs", None)) or 0.0
                for fc in function_calls
            )
        )

        self._emit(ev.LATENCY_BREAKDOWN, value_ms=_round_ms(e2e_ms), detail=detail)

    @_never_raises
    def on_first_bot_speech_latency(self, latency_seconds: float) -> None:
        """Time from client connected to the first word of the greeting."""
        self._emit(ev.FIRST_BOT_SPEECH_LATENCY, value_ms=_to_ms(latency_seconds))

    @_never_raises
    def on_pipeline_error(self, frame: Any) -> None:
        """An ErrorFrame reached the pipeline worker."""
        if getattr(frame, "id", None) is not None and self._seen(frame):
            return
        self._emit(
            ev.PIPELINE_ERROR,
            severity=ev.SEVERITY_ERROR,
            detail={
                "fatal": bool(getattr(frame, "fatal", False)),
                "error": str(getattr(frame, "error", frame))[:300],
            },
        )

    @_never_raises
    def on_heartbeat_timeout(self) -> None:
        """No heartbeat frame came back: the pipeline is blocked somewhere."""
        self._emit(ev.HEARTBEAT_TIMEOUT, severity=ev.SEVERITY_WARN)

    @_never_raises
    def on_pipeline_idle_timeout(self) -> None:
        """No frames at all for the configured idle window."""
        self._emit(ev.PIPELINE_IDLE_TIMEOUT, severity=ev.SEVERITY_WARN)

    @_never_raises
    def call_ended(
        self,
        reason: str | None,
        duration_s: float | None = None,
        end_reason: str | None = None,
    ) -> None:
        """Emit the one row per call that carries the whole summary.

        ``reason`` is the business disposition (LLM-extracted when there is
        one); ``end_reason`` is the raw ``EndTaskReason`` that actually ended
        the call. KPIs on how calls terminate must read ``end_reason``.
        """
        if self._call_ended:
            return
        self._call_ended = True
        # Disarmed before the summary is emitted, so the last row of the call
        # really is the last one and no watchdog fires into a closed call.
        self._cancel_watchdogs()
        if duration_s is None:
            duration_s = time.monotonic() - self._started_at
        self._emit(
            ev.CALL_ENDED,
            value_ms=duration_s * 1000.0,
            detail={
                "reason": reason or "unknown",
                "end_reason": end_reason or "unknown",
                "duration_s": round(duration_s, 2),
                "turns": self._turn,
                "user_speech": self._user_speech_seen,
                "mute_ms_total": _round_ms(self._mute_ms_total),
                "idle_events": self._idle_events,
                "empty_finals": self._empty_finals,
                "hung_function_calls": self._hung_function_calls,
                "silent_after_turn": self._silent_after_turn,
                "refusals_dropped": self._refusals_dropped,
                "llm_empty_responses": self._llm_empty_responses,
                "stop_timeouts": self._stop_timeouts,
                "interruptions": self._interruptions,
                "e2e_p50_ms": _p50(self._e2e_samples),
                "e2e_max_ms": _max(self._e2e_samples),
                "user_turn_p50_ms": _p50(self._user_turn_samples),
                "user_turn_max_ms": _max(self._user_turn_samples),
                "host": ev.host_name(),
            },
        )

    async def cleanup(self):
        """Cancel the watchdogs. Must be called when the run is over."""
        self._cancel_watchdogs()
        tasks = list(self._watchdog_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------ watchdogs

    async def _hung_watchdog(self, tool_call_id: str) -> None:
        try:
            await asyncio.sleep(self._hung_after_s)
            if self._call_ended:
                # The call is over and its summary is already out: a late row
                # would describe a wait nobody was left to notice.
                return
            entry = self._function_calls.get(tool_call_id)
            if entry is None:
                return
            name, started = entry
            self._hung_function_calls += 1
            # The entry is kept so function_call_ended can still report the
            # real duration if the call eventually returns.
            self._emit(
                ev.FUNCTION_CALL_HUNG,
                severity=ev.SEVERITY_WARN,
                value_ms=(time.monotonic() - started) * 1000.0,
                detail={"name": name, "tool_call_id": tool_call_id},
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._log_internal_error("_hung_watchdog", e)
        finally:
            self._hung_tasks.pop(tool_call_id, None)

    async def _silence_watchdog(self, turn: int) -> None:
        try:
            await asyncio.sleep(self._silent_after_s)
            # A turn left unanswered because the call ended is not a silence:
            # the caller hung up, the bot was never given the chance.
            if self._call_ended or self._bot_spoke_since_user_turn:
                return
            elapsed_ms = 0.0
            if self._last_user_turn_stopped_at is not None:
                elapsed_ms = (
                    time.monotonic() - self._last_user_turn_stopped_at
                ) * 1000.0
            self._silent_after_turn += 1
            # In-flight function calls do NOT suppress this event: a hung
            # transition is one of the causes we are looking for, so it belongs
            # in the snapshot rather than in a filter.
            self._emit(
                ev.BOT_SILENT_AFTER_USER_TURN,
                severity=ev.SEVERITY_WARN,
                value_ms=_round_ms(elapsed_ms),
                turn=turn,
                detail={
                    "elapsed_ms": _round_ms(elapsed_ms),
                    "muted": self._muted,
                    # Both shapes on purpose: the list says WHICH call is
                    # holding the turn, the count is what SQL can filter on
                    # without unnesting a JSON array.
                    "function_calls_in_progress": [
                        name for name, _ in self._function_calls.values()
                    ],
                    "function_calls_in_progress_n": len(self._function_calls),
                    "llm_response_open": self._llm_open,
                    "llm_chars_since_turn": self._llm_chars_since_turn,
                    "refusals_dropped_since_turn": self._refusals_since_turn,
                    "tts_started_since_turn": self._tts_started_since_turn,
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._log_internal_error("_silence_watchdog", e)

    def _cancel_watchdogs(self) -> None:
        """Disarm every pending watchdog: there is nothing left to diagnose."""
        self._cancel_silence_watchdog()
        for task in list(self._hung_tasks.values()):
            self._cancel_task(task)
        self._hung_tasks.clear()

    def _cancel_silence_watchdog(self) -> None:
        self._cancel_task(self._silent_task)
        self._silent_task = None

    def _spawn(self, coroutine, name: str) -> asyncio.Task | None:
        """Track watchdog tasks so finalization can cancel and await them."""
        try:
            task = asyncio.create_task(coroutine, name=f"call-events::{name}")
            self._watchdog_tasks.add(task)
            task.add_done_callback(self._watchdog_tasks.discard)
            return task
        except RuntimeError as e:
            # No running loop: nothing to arm the watchdog on.
            coroutine.close()
            logger.debug(f"Diagnostics watchdog {name} not started: {e}")
            return None

    def _cancel_task(self, task: asyncio.Task | None) -> None:
        if task is not None and not task.done():
            task.cancel()

    # ---------------------------------------------------------------- emit

    def _emit(
        self,
        event: str,
        *,
        severity: str = ev.SEVERITY_INFO,
        value_ms: float | None = None,
        turn: int | None = None,
        detail: dict | None = None,
    ) -> None:
        if self._call_ended and event != ev.CALL_ENDED:
            return
        node_id, node_name = self._current_node()
        self._sink.emit(
            CallEvent(
                event=event,
                run_id=self._run_id,
                org_id=self._org_id,
                workflow_id=self._workflow_id,
                turn=self._turn if turn is None else turn,
                severity=severity,
                value_ms=None if value_ms is None else round(value_ms, 1),
                node_id=node_id,
                node_name=node_name,
                detail=detail or {},
            )
        )

    def _mute_snapshot(self) -> dict:
        """Everything that can explain why the user is (still) muted."""
        queued_state = getattr(self._engine, "_queued_speech_mute_state", None)
        if queued_state is None:
            playback = getattr(self._engine, "speech_playback", None)
            if playback is not None:
                pending = [
                    s for s in playback.pending.values() if s.mute_user and not s.done
                ]
                # Preserve the old idle/waiting/playing vocabulary using the
                # current request-owned playback boundaries.
                queued_state = (
                    "playing"
                    if any(s.started for s in pending)
                    else "waiting"
                    if pending
                    else "idle"
                )
        return {
            "first_bot_complete_pending": not self._first_bot_completed,
            "queued_speech_state": str(queued_state) if queued_state else "unknown",
            "pipeline_muted": bool(getattr(self._engine, "_mute_pipeline", False)),
            # Same name, same type everywhere: the "_n" suffix is the count.
            "function_calls_in_progress_n": len(self._function_calls),
        }

    def _current_node(self) -> tuple[str | None, str | None]:
        if self._source_node is not None:
            return self._source_node
        agent = getattr(self._engine, "active_agent", None)
        node = getattr(agent, "current_node", None)
        if node is None:
            return None, None
        node_id = getattr(node, "id", None)
        node_name = getattr(node, "name", None)
        return (
            None if node_id is None else str(node_id),
            None if node_name is None else str(node_name),
        )

    def _log_internal_error(self, where: str, error: Exception) -> None:
        """Report the first failure loudly, the rest quietly."""
        message = f"Diagnostics {where} failed: {type(error).__name__}: {error}"
        if self._internal_error_logged:
            logger.debug(message)
            return
        self._internal_error_logged = True
        logger.warning(message)


def _class_name(obj: Any) -> str | None:
    return None if obj is None else type(obj).__name__


def _turn_to_audio_ms(breakdown: Any) -> float | None:
    """Bot response: from the user turn RELEASE to the FIRST byte of TTS audio, in ms.

    Differs from `e2e_ms` (pipecat, `on_latency_measured`) at both ends, on purpose:

      * it starts at the turn release (`user_turn_start_time + user_turn_secs`), not at
        user silence, so it EXCLUDES the VAD silence window and the end-of-turn
        strategy wait. Those are deliberate configuration — changed through
        `stop_secs`, not by optimizing the pipeline — and are reported separately as
        `user_turn_ms`;
      * it ends at the first byte of TTS audio, not at `BotStartedSpeakingFrame`, so it
        EXCLUDES the transport's playback buffer. It measures when the bot is ready to
        speak, the only part of the path this service controls.

    Both ends come from the `breakdown` ALONE: `user_turn_start_time` and the TTFB
    `start_time`s share one time base (`time.time()`), so the subtraction is valid. If
    either end is missing the value is `None` — not zero, and not a fallback to
    `e2e_ms`: two definitions in one column publish an average of whichever
    measurement happened to be available.

    With several TTS TTFBs in the cycle (a reconnect, or the greeting window) the FIRST
    audio wins, since that is what the caller hears; the per-stage columns keep the
    worst instead, because they answer a different question.
    """
    start = getattr(breakdown, "user_turn_start_time", None)
    turn_secs = getattr(breakdown, "user_turn_secs", None)
    if not isinstance(start, (int, float)) or not isinstance(turn_secs, (int, float)):
        return None
    released = start + turn_secs

    first_audio = None
    for metrics in getattr(breakdown, "ttfb", None) or []:
        if ttfb_kind(getattr(metrics, "processor", None)) != "tts":
            continue
        started = getattr(metrics, "start_time", None)
        duration = getattr(metrics, "duration_secs", None)
        if not isinstance(started, (int, float)) or not isinstance(
            duration, (int, float)
        ):
            continue
        ended = started + duration
        if first_audio is None or ended < first_audio:
            first_audio = ended

    # Audio that precedes the turn release is not a negative latency: it is a TTFB
    # left in the accumulator from the previous turn (or an interruption). Discard it.
    if first_audio is None or first_audio < released:
        return None
    return (first_audio - released) * 1000.0


def _to_ms(seconds: float | None) -> float | None:
    return None if seconds is None else float(seconds) * 1000.0


def _round_ms(value: float | None) -> float | None:
    return None if value is None else round(float(value), 1)


def _p50(samples: list[float]) -> float | None:
    return _round_ms(statistics.median(samples)) if samples else None


def _max(samples: list[float]) -> float | None:
    return _round_ms(max(samples)) if samples else None
