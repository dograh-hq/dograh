"""Observe answer turns while suppressing their workflow inference triggers.

The gate runs synchronously in the aggregator's push: committing text and
dropping its trigger complete before on_user_turn_stopped grants permission.
Upstream UserStoppedSpeakingFrame is deliberately NOT a permission signal.
"""

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass

from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    LLMContextFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameProcessor

from api.enums import AnswerAction
from api.schemas.answer_supervisor import AnswerSupervisorConfig
from api.services.pipecat.answer_classification import (
    MachineSubtype,
    classify_machine_utterance,
)


@dataclass(frozen=True)
class AnswerVerdict:
    action: AnswerAction
    reason: str
    subtype: MachineSubtype | None = None


class AnswerContextGate(FrameProcessor):
    def __init__(self):
        # No queue between the aggregator commit and this drop acknowledgement.
        # Keep this processor free of waits, classification, and engine actions.
        super().__init__(enable_direct_mode=True)
        self.closed = True
        self.dropped_contexts = 0
        self.held_user_messages: dict[int, dict] = {}

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame) and self.closed:
            self.dropped_contexts += 1
            for message in frame.context.messages:
                if isinstance(message, dict) and message.get("role") == "user":
                    self.held_user_messages[id(message)] = message
            return
        await self.push_frame(frame, direction)


class AnswerSupervisor(FrameProcessor):
    def __init__(
        self,
        config: AnswerSupervisorConfig,
        *,
        context: LLMContext,
        classify: Callable[[str], Awaitable[MachineSubtype]] | None = None,
    ):
        super().__init__()
        self.config = config
        self._context = context
        self._classify = classify
        self._gate = AnswerContextGate()
        self._armed_at: float | None = None
        self._onset: float | None = None
        self._screening = False
        self._screening_idle = asyncio.Event()
        self._committed = False
        self._released = False
        self._closed = False
        self._closed_event = asyncio.Event()
        self._epoch = 0
        self._verdict: AnswerVerdict | None = None
        self._changed = asyncio.Event()
        self._supervision_tasks: set[asyncio.Task] = set()
        self._utterance_task: asyncio.Task | None = None
        self._classifier_task: asyncio.Task | None = None
        self._turn_texts: list[str] = []

    def llm_gate(self) -> AnswerContextGate:
        return self._gate

    @property
    def blocks_workflow(self) -> bool:
        return not self._released

    def bind(self, aggregator) -> None:
        aggregator.add_event_handler("on_user_turn_stopped", self._on_turn_stopped)

    async def wait_closed(self):
        await self._closed_event.wait()

    def _spawn(self, coro: Coroutine) -> asyncio.Task:
        """Track this supervisor's managed tasks for cancellation at transitions.

        Transitions request cancellation synchronously to preserve frame ordering;
        close() awaits completion through the shared task manager.
        """
        task = self.create_task(coro)
        self._supervision_tasks.add(task)
        task.add_done_callback(self._supervision_tasks.discard)
        return task

    def arm(self) -> None:
        """Start the clock at the two-event readiness barrier, before pre-call fetch."""
        if self._armed_at is not None or self._closed:
            return
        self._armed_at = asyncio.get_running_loop().time()
        self._spawn(self._listening_timeout())
        if self._onset is not None:
            self._start_utterance_timer()

    async def _listening_timeout(self):
        """Allow the opening after an initial window with no detected speech.

        Started once by arm(), this waits listening_window_ms. If no speech
        has started (_epoch is still zero), publish a silent_window release
        verdict so a silent answer cannot hold the workflow indefinitely.
        Any speech onset disables this fallback for the rest of supervision.
        Screening and committed decisions are excluded. A release permits
        the engine to proceed; it does not classify the answer as human.
        """
        await asyncio.sleep(self.config.listening_window_ms / 1000)
        if self._epoch == 0 and not self._screening and not self._committed:
            self._publish(
                AnswerVerdict(AnswerAction.RELEASE, "silent_window"),
                strategy="listening_timeout",
                listening_window_ms=self.config.listening_window_ms,
            )

    def _start_utterance_timer(self):
        if self._utterance_task:
            self._utterance_task.cancel()
        self._utterance_task = self._spawn(self._utterance_timeout(self._epoch))

    async def _utterance_timeout(self, epoch):
        """Bound the wait for a speaking turn to finish with usable text.

        The machine_utterance_cap_ms timer starts on UserStartedSpeakingFrame,
        or at arm() if the user turn started before readiness. A resolved turn with
        nonempty text cancels it; LLM classification has a separate budget.
        If it expires for the same speech epoch before a decision is committed,
        drop the call with machine_timeout, including during screening.
        The epoch check prevents an older timer from deciding a newer turn.
        """
        await asyncio.sleep(self.config.machine_utterance_cap_ms / 1000)
        if epoch != self._epoch or self._closed or self._committed:
            return
        self._publish(
            AnswerVerdict(AnswerAction.DROP, "machine_timeout"),
            strategy="utterance_timeout",
            machine_utterance_cap_ms=self.config.machine_utterance_cap_ms,
        )

    def _speech_started(self):
        if self._closed or self._released or self._committed:
            return
        if self._onset is not None:
            return  # Duplicate starts must not extend the same turn's deadline.
        self._onset = asyncio.get_running_loop().time()
        self._epoch += 1
        self._verdict = None  # Revoke unused permission during a slow pre-call fetch.
        self._changed.set()
        if self._screening:
            self._screening_idle.clear()

        if self._classifier_task:
            self._classifier_task.cancel()
        if self._armed_at is not None:
            self._start_utterance_timer()

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, (CancelFrame, EndFrame)):
            await self.close()
        elif isinstance(frame, UserStartedSpeakingFrame):
            self._speech_started()
        await self.push_frame(frame, direction)

    async def _on_turn_stopped(self, aggregator, strategy, message):
        if self._closed or self._released or self._committed:
            return
        text = (message.content or "").strip()
        if not text:
            return  # Music/no STT evidence is never a human answer.
        if self._screening:
            # Keep the screening deadline paused through classification.
            self._screening_idle.clear()
        # Speech can resume while classification is pending or its verdict is
        # uncommitted. Keep the evidence outside the cancelled task.
        self._turn_texts.append(text)
        text = " ".join(self._turn_texts)
        now = asyncio.get_running_loop().time()
        duration = now - self._onset if self._onset is not None else float("inf")
        signals = {
            "turn_stop_strategy": str(strategy),
            "duration_ms": round(duration * 1000) if self._onset is not None else None,
            "human_utterance_max_ms": self.config.human_utterance_max_ms,
            "transcript_chars": len(text),
        }
        self._onset = None
        if self._utterance_task:
            self._utterance_task.cancel()
        subtype = classify_machine_utterance(text)
        # IVR instructions, clipped screener prompts, and screening acknowledgments
        # can be brief. Recognize them before applying the short-human-turn
        # shortcut, along with machine prompts while waiting for the subscriber.
        machine_turn = subtype in (
            MachineSubtype.IVR,
            MachineSubtype.SCREENER,
            MachineSubtype.SCREENING_WAIT,
        ) or (self._screening and subtype != MachineSubtype.UNKNOWN)
        # A short continuation must not bypass classification of earlier speech.
        if (
            len(self._turn_texts) == 1
            and duration < self.config.human_utterance_max_ms / 1000
            and not machine_turn
        ):
            self._publish(
                AnswerVerdict(
                    AnswerAction.RELEASE, "human_turn", MachineSubtype.CONVERSATION
                ),
                strategy="duration_threshold",
                **signals,
            )
            return

        self._classifier_task = self._spawn(
            self._classify_turn(text, self._epoch, subtype=subtype, **signals)
        )

    async def _classify_turn(self, text, epoch, *, subtype=None, **signals):
        signals["transcript_chars"] = len(text)
        strategy = "transcript_patterns"
        if subtype is None:
            subtype = classify_machine_utterance(text)
        if subtype == MachineSubtype.UNKNOWN and self._classify:
            strategy = "llm_classifier"
            signals["classify_budget_ms"] = self.config.classify_budget_ms
            try:
                async with asyncio.timeout(self.config.classify_budget_ms / 1000):
                    subtype = await self._classify(text)
                signals["classifier_status"] = "completed"
            except TimeoutError:
                signals["classifier_status"] = "timeout"
                self._log("classifier_timeout", strategy=strategy, **signals)
            except Exception as exc:
                signals["classifier_status"] = "error"
                signals["error_type"] = type(exc).__name__
                self._log("classifier_error", strategy=strategy, **signals)
        if epoch != self._epoch or self._committed or self._closed:
            return
        if subtype == MachineSubtype.SCREENING_WAIT:
            if self._screening:
                # Preserve the deadline from playback; acknowledgments must not
                # extend the wait or consume another screening announcement.
                self._discard_held_turns()
                self._screening_idle.set()
            else:
                # If this is the first detected announcement, it still needs a
                # bounded silent wait instead of the initial fail-open timers.
                self.begin_screening_wait()
            self._log(
                "screening_wait",
                subtype,
                strategy=strategy,
                action="wait",
                screening_wait_ms=self.config.screening_wait_ms,
                **signals,
            )
            return
        actions = {
            MachineSubtype.VOICEMAIL: (
                AnswerAction.LEAVE_MESSAGE
                if self.config.voicemail_action == "leave_message"
                else AnswerAction.DROP
            ),
            MachineSubtype.NO_MESSAGE: AnswerAction.DROP,
            MachineSubtype.SCREENER: AnswerAction.SCREEN_THEN_REARM,
            MachineSubtype.IVR: AnswerAction.DROP,
            MachineSubtype.CONVERSATION: AnswerAction.RELEASE,
            MachineSubtype.UNKNOWN: AnswerAction.RELEASE,
        }
        if self._screening and subtype == MachineSubtype.UNKNOWN:
            self._log("screening_unknown", subtype, strategy=strategy, **signals)
            self._discard_held_turns()
            self._screening_idle.set()
            return
        self._publish(
            AnswerVerdict(actions[subtype], subtype.value.lower(), subtype),
            strategy=strategy,
            **signals,
        )

    def _publish(self, verdict: AnswerVerdict, *, strategy: str, **signals):
        if self._closed or self._released or self._committed:
            return
        self._verdict = verdict
        self._changed.set()
        self._log(
            verdict.reason,
            verdict.subtype,
            strategy=strategy,
            action=verdict.action.value,
            **signals,
        )

    def _log(self, event, subtype=None, *, strategy: str, **signals):
        elapsed = (
            round((asyncio.get_running_loop().time() - self._armed_at) * 1000)
            if self._armed_at is not None
            else None
        )
        logger.info(
            "Answer supervisor event={} elapsed_ms={} subtype={} strategy={} {}",
            event,
            elapsed,
            subtype,
            strategy,
            " ".join(f"{key}={value!r}" for key, value in signals.items()),
        )

    async def wait_for_verdict(self) -> AnswerVerdict:
        while self._verdict is None:
            self._changed.clear()
            await self._changed.wait()
        return self._verdict

    def commit(self) -> None:
        """The engine has accepted a verdict and is starting its action."""
        self._committed = True

    def release(self) -> None:
        """Hand inference to the workflow after its opening has played."""
        if self._closed:
            return
        self._released = True
        self._committed = True
        self._gate.closed = False
        self._gate.held_user_messages.clear()
        self._turn_texts.clear()
        for task in self._supervision_tasks:
            task.cancel()

    def _discard_held_turns(self):
        held = self._gate.held_user_messages
        self._context.set_messages(
            [m for m in self._context.messages if id(m) not in held]
        )
        held.clear()
        self._turn_texts.clear()

    def begin_screening_wait(self) -> None:
        """A dedicated silent wait; the ordinary fail-open window cannot release it.

        Keep the user aggregator live. Only inference is gated, so a subscriber's
        short answer resolves normally. Delete committed machine turns before it.
        """
        self._discard_held_turns()
        self._epoch += 1
        self._screening = True
        self._screening_idle.set()
        self._committed = False
        self._onset = None
        self._verdict = None
        self._gate.closed = True
        for task in self._supervision_tasks:
            task.cancel()
        self._spawn(self._screening_timeout())

    async def _screening_timeout(self):
        """Keep the original deadline, but let an active turn resolve first.

        Speech holds this timer through usable turn completion and classification.
        The utterance cap bounds missing transcripts; UNKNOWN and SCREENING_WAIT
        resume the existing deadline. Actionable verdicts stay protected until
        accepted or revoked by another turn.
        """
        await asyncio.sleep(self.config.screening_wait_ms / 1000)
        while not self._screening_idle.is_set():
            # Recheck after waking: another speech start can clear the event
            # before this task resumes.
            await self._screening_idle.wait()
        if self._verdict is not None:
            return
        self._publish(
            AnswerVerdict(AnswerAction.DROP, "screening_timeout"),
            strategy="screening_timeout",
            screening_wait_ms=self.config.screening_wait_ms,
        )

    async def close(self):
        if self._closed:
            return
        self._closed = True
        self._turn_texts.clear()
        self._closed_event.set()
        self._gate.closed = True
        self._verdict = AnswerVerdict(AnswerAction.CANCELLED, "pipeline_ended")
        self._changed.set()
        await asyncio.gather(
            *(self.cancel_task(task) for task in tuple(self._supervision_tasks))
        )

    async def cleanup(self):
        await self.close()
        await super().cleanup()
