"""Answer startup regressions using real Pipecat queues and turn aggregation."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    LLMContextFrame,
    ProposedUserStartedSpeakingFrame,
    ProposedUserStoppedSpeakingFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserIdleTimeoutUpdateFrame,
    UserStartedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMUserAggregator,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.turns.user_mute import FirstSpeechUserMuteStrategy
from pipecat.turns.user_start import ExternalUserTurnStartStrategy
from pipecat.turns.user_stop import ExternalUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from api.schemas.answer_supervisor import AnswerSupervisorConfig
from api.services.pipecat.answer_classification import MachineSubtype
from api.services.pipecat.processors.answer_supervisor import AnswerSupervisor
from api.services.pipecat.worker_runner import run_pipeline_worker


class Capture(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.frames = []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        self.frames.append(frame)
        await self.push_frame(frame, direction)


@asynccontextmanager
async def call(*, classify=None, before_drop=None, arm_on_start=True, **settings):
    config = AnswerSupervisorConfig(
        listening_window_ms=80,
        human_utterance_max_ms=40,
        machine_utterance_cap_ms=250,
        classify_budget_ms=60,
        screening_wait_ms=180,
    ).model_copy(update=settings)
    context = LLMContext()
    supervisor = AnswerSupervisor(config, context=context, classify=classify)
    if before_drop is not None:
        original = supervisor.llm_gate().process_frame

        async def delayed_gate(frame, direction):
            if isinstance(frame, LLMContextFrame):
                await before_drop()
            await original(frame, direction)

        supervisor.llm_gate().process_frame = delayed_gate
    aggregator = LLMUserAggregator(
        context,
        params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                start=[ExternalUserTurnStartStrategy(enable_interruptions=False)],
                stop=[ExternalUserTurnStopStrategy()],
            ),
            user_mute_strategies=[FirstSpeechUserMuteStrategy()],
        ),
    )
    supervisor.bind(aggregator)
    capture = Capture()
    worker = PipelineWorker(
        Pipeline([supervisor, aggregator, supervisor.llm_gate(), capture]),
        enable_rtvi=False,
    )
    started = asyncio.Event()

    @worker.event_handler("on_pipeline_started")
    async def on_started(*_):
        if arm_on_start:
            supervisor.arm()
        started.set()

    runner = asyncio.create_task(run_pipeline_worker(worker))

    async def say(text, *, duration=0.01):
        await worker.queue_frame(VADUserStartedSpeakingFrame())
        await worker.queue_frame(ProposedUserStartedSpeakingFrame())
        await asyncio.sleep(duration)
        # Finalized transcripts can be overtaken by high-priority stop frames.
        await worker.queue_frame(
            TranscriptionFrame(text, "synthetic", "", finalized=True)
        )
        await worker.queue_frame(VADUserStoppedSpeakingFrame())
        await worker.queue_frame(ProposedUserStoppedSpeakingFrame())

    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        yield SimpleNamespace(
            supervisor=supervisor,
            context=context,
            capture=capture,
            worker=worker,
            say=say,
            aggregator=aggregator,
        )
    finally:
        if not runner.done():
            await worker.queue_frame(EndFrame())
        await asyncio.wait_for(runner, timeout=3)


async def verdict(c):
    return await asyncio.wait_for(c.supervisor.wait_for_verdict(), timeout=1)


@pytest.mark.asyncio
async def test_short_hello_is_committed_and_trigger_dropped_before_release():
    async with call() as c:
        await c.say("Hello?")
        assert (await verdict(c)).action == "release"
        # This acknowledgement must already hold when permission is granted.
        assert c.supervisor.llm_gate().dropped_contexts == 1
        assert c.context.messages == [{"role": "user", "content": "Hello?"}]
        c.supervisor.release()
        await c.worker.queue_frame(TTSSpeakFrame("Configured opening"))
        await asyncio.sleep(0.02)
        assert not any(isinstance(f, LLMContextFrame) for f in c.capture.frames)
        assert sum(isinstance(f, TTSSpeakFrame) for f in c.capture.frames) == 1
        await c.say("I would like an appointment")
        await asyncio.sleep(0.02)
        assert sum(isinstance(f, LLMContextFrame) for f in c.capture.frames) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text, duration",
    [
        ("Hello, Synthetic Caller speaking.", 0.01),
        ("Synthetic Caller is away. Please leave a message.", 0.06),
        ("My account number is synthetic-private-value.", 0.06),
    ],
)
async def test_decision_logs_omit_caller_transcripts(monkeypatch, text, duration):
    from api.services.pipecat.processors import answer_supervisor

    messages = []
    monkeypatch.setattr(
        answer_supervisor,
        "logger",
        SimpleNamespace(info=lambda fmt, *args: messages.append(fmt.format(*args))),
    )
    async with call(classify=AsyncMock(return_value=MachineSubtype.CONVERSATION)) as c:
        await c.say(text, duration=duration)
        await verdict(c)
        assert messages
        assert all(text not in message for message in messages)
        assert any(f"transcript_chars={len(text)}" in message for message in messages)


@pytest.mark.asyncio
async def test_permission_waits_for_gate_acknowledgement_under_queue_backlog():
    reached_gate, allow_drop = asyncio.Event(), asyncio.Event()

    async def before_drop():
        reached_gate.set()
        await allow_drop.wait()

    async with call(before_drop=before_drop) as c:
        pending = asyncio.create_task(c.supervisor.wait_for_verdict())
        try:
            await c.say("Hello?")
            await asyncio.wait_for(reached_gate.wait(), 1)
            await asyncio.sleep(0.02)
            assert not pending.done(), (
                "Permission escaped before the old trigger was consumed"
            )
            allow_drop.set()
            assert (await asyncio.wait_for(pending, 1)).action == "release"
            c.supervisor.release()
            await asyncio.sleep(0.02)
            assert not any(isinstance(f, LLMContextFrame) for f in c.capture.frames)
        finally:
            allow_drop.set()
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_silent_answer_waits_for_window():
    async with call() as c:
        pending = asyncio.create_task(c.supervisor.wait_for_verdict())
        await asyncio.sleep(0.025)
        assert not pending.done()
        assert (await asyncio.wait_for(pending, 1)).action == "release"


@pytest.mark.asyncio
async def test_speech_during_pre_call_fetch_revokes_unused_silent_permission():
    async with call() as c:
        assert (await verdict(c)).action == "release"
        await c.worker.queue_frame(VADUserStartedSpeakingFrame())
        await c.worker.queue_frame(ProposedUserStartedSpeakingFrame())
        await asyncio.sleep(0.02)
        pending = asyncio.create_task(c.supervisor.wait_for_verdict())
        await asyncio.sleep(0.02)
        assert not pending.done()
        await c.worker.queue_frame(CancelFrame())
        assert (await asyncio.wait_for(pending, 1)).action == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "voicemail_action, expected_action",
    [("hangup", "drop"), ("leave_message", "leave_message")],
)
@pytest.mark.parametrize(
    "text, uses_llm",
    [
        ("Please leave a message after the tone.", False),
        ("You have reached Alex. I am away.", True),
    ],
)
async def test_long_turn_is_held_until_final_transcript_and_classified(
    voicemail_action, expected_action, text, uses_llm
):
    classifier = AsyncMock(return_value=MachineSubtype.VOICEMAIL)
    async with call(classify=classifier, voicemail_action=voicemail_action) as c:
        await c.say(text, duration=0.07)
        result = await verdict(c)
        assert result.action == expected_action
        assert result.subtype == MachineSubtype.VOICEMAIL
        assert not any(isinstance(f, LLMContextFrame) for f in c.capture.frames)
        if uses_llm:
            classifier.assert_awaited_once_with(text)
        else:
            classifier.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [0.01, 0.07])
@pytest.mark.parametrize(
    "text",
    [
        "for calling. I'll see if this person is available.",
        "Tell me your name and reason for calling.",
    ],
)
async def test_screener_prompt_stays_gated_regardless_of_duration(text, duration):
    classifier = AsyncMock(return_value=MachineSubtype.CONVERSATION)
    async with call(classify=classifier) as c:
        await c.say(text, duration=duration)
        result = await verdict(c)
        assert result.action == "screen_then_rearm"
        assert result.subtype == MachineSubtype.SCREENER
        assert c.supervisor.blocks_workflow
        assert c.supervisor.llm_gate().closed
        assert c.supervisor.llm_gate().dropped_contexts == 1
        assert not any(isinstance(f, LLMContextFrame) for f in c.capture.frames)
        classifier.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("screening", [False, True])
@pytest.mark.parametrize("duration", [0.01, 0.07])
@pytest.mark.parametrize(
    "text",
    [
        "Press zero.",
        "Dial six.",
        "Press seven.",
        "Dial eight.",
        "Press nine.",
        "Press 1.",
    ],
)
async def test_ivr_prompt_stays_gated_regardless_of_duration(text, duration, screening):
    classifier = AsyncMock(return_value=MachineSubtype.CONVERSATION)
    async with call(classify=classifier) as c:
        if screening:
            c.supervisor.begin_screening_wait()
        await c.say(text, duration=duration)
        result = await verdict(c)
        assert result.action == "drop"
        assert result.subtype == MachineSubtype.IVR
        assert c.supervisor.blocks_workflow
        assert c.supervisor.llm_gate().closed
        assert c.supervisor.llm_gate().dropped_contexts == 1
        assert not any(isinstance(f, LLMContextFrame) for f in c.capture.frames)
        classifier.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("screening", [False, True])
async def test_new_human_turn_cancels_stale_machine_classification(screening):
    entered, cancelled = asyncio.Event(), asyncio.Event()
    transcripts = []

    async def classify(text):
        transcripts.append(text)
        if len(transcripts) > 1:
            return MachineSubtype.CONVERSATION
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async with call(
        classify=classify, classify_budget_ms=500, screening_wait_ms=40
    ) as c:
        if screening:
            c.supervisor.begin_screening_wait()
        await c.say("A long ambiguous answer", duration=0.07)
        await asyncio.wait_for(entered.wait(), 1)
        await c.say("Hello?")
        result = await verdict(c)
        assert result.action == "release"
        assert result.reason == "conversation"
        assert cancelled.is_set()
        assert transcripts == [
            "A long ambiguous answer",
            "A long ambiguous answer Hello?",
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("continuation_duration", [0.01, 0.07])
async def test_repeated_speech_preserves_all_cancelled_classification_text(
    continuation_duration,
):
    entered = asyncio.Queue()
    transcripts, cancelled = [], []

    async def classify(text):
        transcripts.append(text)
        if len(transcripts) == 3:
            return MachineSubtype.VOICEMAIL
        entered.put_nowait(text)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(text)

    async with call(classify=classify, classify_budget_ms=1000) as c:
        await c.say("You have reached Alex.", duration=0.07)
        assert await asyncio.wait_for(entered.get(), 1) == "You have reached Alex."
        await c.say("I am away.", duration=continuation_duration)
        assert await asyncio.wait_for(entered.get(), 1) == (
            "You have reached Alex. I am away."
        )
        await c.say("Thanks for calling.", duration=continuation_duration)
        assert (await verdict(c)).action == "drop"
        assert transcripts == [
            "You have reached Alex.",
            "You have reached Alex. I am away.",
            "You have reached Alex. I am away. Thanks for calling.",
        ]
        assert cancelled == transcripts[:2]


@pytest.mark.asyncio
async def test_machine_pattern_spans_cancelled_classification_turns():
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def classify(_):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    classifier = AsyncMock(side_effect=classify)
    async with call(classify=classifier, classify_budget_ms=1000) as c:
        await c.say("Please leave", duration=0.07)
        await asyncio.wait_for(entered.wait(), 1)
        await c.say("a message.")
        result = await verdict(c)
        assert result.action == "drop"
        assert result.subtype == MachineSubtype.VOICEMAIL
        assert cancelled.is_set()
        classifier.assert_awaited_once_with("Please leave")


@pytest.mark.asyncio
async def test_obsolete_listen_mode_cannot_bypass_machine_classification():
    classifier = AsyncMock()
    async with call(classify=classifier, supervisor_mode="listen") as c:
        await c.say("Please leave a message after the tone.", duration=0.07)
        assert (await verdict(c)).action == "drop"
        classifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_screening_silence_does_not_release_at_ordinary_window():
    async with call() as c:
        c.supervisor.begin_screening_wait()
        pending = asyncio.create_task(c.supervisor.wait_for_verdict())
        await asyncio.sleep(0.11)
        assert not pending.done()
        result = await asyncio.wait_for(pending, 1)
        assert result.action == "drop"
        assert result.reason == "screening_timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start_frame", [VADUserStartedSpeakingFrame, ProposedUserStartedSpeakingFrame]
)
@pytest.mark.parametrize(
    "subtype, expected_action",
    [
        (MachineSubtype.CONVERSATION, "release"),
        (MachineSubtype.VOICEMAIL, "drop"),
        (MachineSubtype.SCREENING_WAIT, "drop"),
        (MachineSubtype.UNKNOWN, "drop"),
    ],
)
async def test_screening_deadline_waits_for_active_turn_and_classification(
    start_frame, subtype, expected_action
):
    entered, finish_classification = asyncio.Event(), asyncio.Event()

    async def classify(_):
        entered.set()
        await finish_classification.wait()
        return subtype

    async with call(
        classify=classify,
        screening_wait_ms=120,
        machine_utterance_cap_ms=1000,
        classify_budget_ms=1000,
    ) as c:
        c.supervisor.begin_screening_wait()
        # Flux proposes starts without a VAD frame; the aggregator broadcasts
        # the resolved UserStartedSpeakingFrame back upstream to the supervisor.
        await c.worker.queue_frame(start_frame())
        if start_frame is VADUserStartedSpeakingFrame:
            await c.worker.queue_frame(ProposedUserStartedSpeakingFrame())
        async with asyncio.timeout(1):
            while not any(
                isinstance(f, UserStartedSpeakingFrame) for f in c.capture.frames
            ):
                await asyncio.sleep(0)

        await asyncio.sleep(0.2)
        assert c.supervisor._verdict is None, "Screening timeout cut off active speech"
        await c.worker.queue_frame(
            TranscriptionFrame(
                "This is Alex speaking.", "synthetic", "", finalized=True
            )
        )
        await c.worker.queue_frame(VADUserStoppedSpeakingFrame())
        await c.worker.queue_frame(ProposedUserStoppedSpeakingFrame())
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(0.02)
        assert c.supervisor._verdict is None, "Screening timeout raced classification"

        finish_classification.set()
        result = await verdict(c)
        assert result.action == expected_action
        if subtype in (MachineSubtype.SCREENING_WAIT, MachineSubtype.UNKNOWN):
            assert result.reason == "screening_timeout"
            assert c.context.messages == []
        else:
            assert result.subtype == subtype


@pytest.mark.asyncio
async def test_screening_timeout_does_not_overwrite_unused_human_permission():
    async with call(screening_wait_ms=120) as c:
        c.supervisor.begin_screening_wait()
        await c.say("Hello?")
        result = await verdict(c)
        assert result.action == "release"
        await asyncio.sleep(0.2)
        assert await verdict(c) == result


@pytest.mark.asyncio
async def test_screening_unresolved_speech_defers_timeout_with_a_bound():
    async with call(screening_wait_ms=80, machine_utterance_cap_ms=200) as c:
        c.supervisor.begin_screening_wait()
        await c.worker.queue_frame(ProposedUserStartedSpeakingFrame())
        await asyncio.sleep(0.12)
        assert c.supervisor._verdict is None
        result = await verdict(c)
        assert result.action == "drop"
        assert result.reason == "machine_timeout"


@pytest.mark.asyncio
async def test_vad_alone_does_not_defer_screening_deadline():
    async with call(screening_wait_ms=80) as c:
        c.supervisor.begin_screening_wait()
        await c.worker.queue_frame(VADUserStartedSpeakingFrame())
        assert (await verdict(c)).reason == "screening_timeout"
        assert c.supervisor._onset is None
        assert c.supervisor._utterance_task is None


@pytest.mark.asyncio
async def test_screening_keeps_human_turns_live_and_removes_machine_context():
    async with call() as c:
        await c.say("Tell me your name and reason for calling.", duration=0.07)
        assert (await verdict(c)).action == "screen_then_rearm"
        # Disable idle detection without a persistent user mute.
        await c.aggregator.queue_frame(UserIdleTimeoutUpdateFrame(timeout=0))
        await c.worker.queue_frame(BotStartedSpeakingFrame())
        await c.worker.queue_frame(BotStoppedSpeakingFrame())
        await asyncio.sleep(0.02)
        c.supervisor.begin_screening_wait()
        await c.say("Hello?")
        assert (await verdict(c)).action == "release"
        assert c.context.messages == [{"role": "user", "content": "Hello?"}]
        c.supervisor.release()
        await asyncio.sleep(0.02)
        assert not any(isinstance(f, LLMContextFrame) for f in c.capture.frames)


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [0.01, 0.07])
@pytest.mark.parametrize(
    "voicemail_action, expected_action",
    [("hangup", "drop"), ("leave_message", "leave_message")],
)
async def test_screening_can_reach_voicemail(
    duration, voicemail_action, expected_action
):
    async with call(voicemail_action=voicemail_action) as c:
        c.supervisor.begin_screening_wait()
        await c.say("Please leave a message after the beep.", duration=duration)
        assert (await verdict(c)).action == expected_action


@pytest.mark.asyncio
@pytest.mark.parametrize("acknowledgment_duration", [0.01, 0.07])
@pytest.mark.parametrize(
    "answer, answer_duration, expected_action",
    [
        ("Hello?", 0.01, "release"),
        ("Hello, this is Alex speaking.", 0.07, "release"),
        ("Please leave a message after the beep.", 0.01, "drop"),
    ],
)
async def test_screening_acknowledgment_waits_for_human_or_voicemail(
    acknowledgment_duration, answer, answer_duration, expected_action
):
    classifier = AsyncMock(return_value=MachineSubtype.CONVERSATION)
    async with call(classify=classifier, screening_wait_ms=1000) as c:
        await c.say("Tell me your name and reason for calling.", duration=0.07)
        assert (await verdict(c)).action == "screen_then_rearm"
        c.supervisor.commit()
        c.supervisor.begin_screening_wait()

        await c.say(
            "Thanks. Please stay on the line.", duration=acknowledgment_duration
        )
        async with asyncio.timeout(1):
            while c.supervisor.llm_gate().dropped_contexts < 2 or c.context.messages:
                await asyncio.sleep(0)
        classifier.assert_not_awaited()
        assert c.supervisor.blocks_workflow
        assert c.supervisor.llm_gate().closed

        # Ringback without transcript evidence must leave the agent silent.
        await c.say("", duration=0.11)
        assert c.supervisor._verdict is None
        assert not any(isinstance(f, LLMContextFrame) for f in c.capture.frames)

        await c.say(answer, duration=answer_duration)
        assert (await verdict(c)).action == expected_action
        assert c.context.messages == [{"role": "user", "content": answer}]
        # With no transcript, ringback does not resolve an aggregator turn; its
        # duration carries into the answer, so a human still needs classification.
        if expected_action == "release":
            classifier.assert_awaited_once_with(answer)
        else:
            classifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_llm_screening_wait_discards_acknowledgment_before_next_answer():
    classifier = AsyncMock(return_value=MachineSubtype.SCREENING_WAIT)
    async with call(classify=classifier, screening_wait_ms=1000) as c:
        c.supervisor.begin_screening_wait()
        await c.say("Thank you. I'll try to get them for you.", duration=0.07)
        async with asyncio.timeout(1):
            while not classifier.await_count or c.context.messages:
                await asyncio.sleep(0)
        assert c.supervisor._verdict is None
        await c.say("Hello?")
        assert (await verdict(c)).reason == "human_turn"
        assert c.context.messages == [{"role": "user", "content": "Hello?"}]
        classifier.assert_awaited_once_with("Thank you. I'll try to get them for you.")


@pytest.mark.asyncio
async def test_repeated_screening_acknowledgments_keep_original_deadline():
    async with call(screening_wait_ms=400) as c:
        c.supervisor.begin_screening_wait()
        deadline = asyncio.get_running_loop().time() + 0.5
        await asyncio.sleep(0.22)
        for count in (1, 2):
            await c.say("Thanks. Please stay on the line.")
            async with asyncio.timeout(1):
                while (
                    c.supervisor.llm_gate().dropped_contexts < count
                    or c.context.messages
                ):
                    await asyncio.sleep(0)
            assert c.supervisor._verdict is None
        async with asyncio.timeout_at(deadline):
            result = await c.supervisor.wait_for_verdict()
        assert result.action == "drop"
        assert result.reason == "screening_timeout"


@pytest.mark.asyncio
async def test_initial_wait_announcement_enters_bounded_screening_wait():
    classifier = AsyncMock()
    async with call(classify=classifier) as c:
        await c.say("Thanks. Please stay on the line.")
        result = await verdict(c)
        assert result.action == "drop"
        assert result.reason == "screening_timeout"
        assert c.context.messages == []
        classifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_screening_rearm_resets_classification_text():
    classifier = AsyncMock(return_value=MachineSubtype.CONVERSATION)
    async with call(classify=classifier) as c:
        await c.say("Tell me your name and reason for calling.", duration=0.07)
        assert (await verdict(c)).action == "screen_then_rearm"
        c.supervisor.commit()
        c.supervisor.begin_screening_wait()
        await c.say("Hello, this is Alex speaking.", duration=0.07)
        assert (await verdict(c)).action == "release"
        classifier.assert_awaited_once_with("Hello, this is Alex speaking.")


@pytest.mark.asyncio
async def test_screening_unknown_resets_classification_text_for_next_answer():
    classifier = AsyncMock(return_value=MachineSubtype.UNKNOWN)
    async with call(classify=classifier, screening_wait_ms=1000) as c:
        c.supervisor.begin_screening_wait()
        await c.say("An ambiguous automated announcement", duration=0.07)
        async with asyncio.timeout(1):
            while not classifier.await_count or c.context.messages:
                await asyncio.sleep(0)
        await c.say("Hello?")
        assert (await verdict(c)).reason == "human_turn"
        classifier.assert_awaited_once_with("An ambiguous automated announcement")


@pytest.mark.asyncio
async def test_untranscribed_user_turn_during_screening_drops_without_classifier():
    classifier = AsyncMock()
    async with call(classify=classifier) as c:
        c.supervisor.begin_screening_wait()
        await c.say("", duration=0.07)
        result = await verdict(c)
        assert result.action == "drop"
        assert result.reason == "machine_timeout"
        classifier.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("screening", [False, True])
async def test_classifier_timeout_applies_answer_policy_and_cancels_inference(
    screening,
):
    cancelled = asyncio.Event()

    async def classify(_):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async with call(classify=classify, screening_wait_ms=40) as c:
        if screening:
            c.supervisor.begin_screening_wait()
        await c.say("A long ambiguous greeting without machine keywords", duration=0.07)
        result = await verdict(c)
        assert result.action == ("drop" if screening else "release")
        if screening:
            assert result.reason == "screening_timeout"
        assert cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start_frame", [UserStartedSpeakingFrame, ProposedUserStartedSpeakingFrame]
)
async def test_unresolved_user_turn_drops_at_utterance_cap(start_frame):
    async with call() as c:
        await c.worker.queue_frame(start_frame())
        result = await verdict(c)
        assert result.action == "drop"
        assert result.reason == "machine_timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize("arm_on_start", [False, True])
async def test_vad_alone_never_sets_onset_or_arms_utterance_timer(arm_on_start):
    async with call(arm_on_start=arm_on_start) as c:
        await c.worker.queue_frame(VADUserStartedSpeakingFrame())
        async with asyncio.timeout(1):
            while not any(
                isinstance(frame, VADUserStartedSpeakingFrame)
                for frame in c.capture.frames
            ):
                await asyncio.sleep(0)
        if not arm_on_start:
            c.supervisor.arm()
        result = await verdict(c)
        assert result.action == "release"
        assert result.reason == "silent_window"
        assert c.supervisor._onset is None
        assert c.supervisor._utterance_task is None


@pytest.mark.asyncio
async def test_user_turn_before_readiness_arms_timer_at_readiness():
    async with call(arm_on_start=False) as c:
        await c.worker.queue_frame(ProposedUserStartedSpeakingFrame())
        async with asyncio.timeout(1):
            while c.supervisor._onset is None:
                await asyncio.sleep(0)
        assert c.supervisor._utterance_task is None
        c.supervisor.arm()
        assert c.supervisor._utterance_task is not None
        assert (await verdict(c)).reason == "machine_timeout"


@pytest.mark.asyncio
async def test_vad_and_duplicate_user_starts_do_not_restart_utterance_timer():
    async with call() as c:
        await c.worker.queue_frame(ProposedUserStartedSpeakingFrame())
        async with asyncio.timeout(1):
            while c.supervisor._utterance_task is None:
                await asyncio.sleep(0)
        onset = c.supervisor._onset
        timer = c.supervisor._utterance_task
        await c.worker.queue_frame(VADUserStartedSpeakingFrame())
        await c.worker.queue_frame(UserStartedSpeakingFrame())
        assert (await verdict(c)).reason == "machine_timeout"
        assert c.supervisor._onset == onset
        assert c.supervisor._utterance_task is timer


@pytest.mark.asyncio
async def test_cancel_wakes_waiter_and_never_releases_opening():
    async with call() as c:
        pending = asyncio.create_task(c.supervisor.wait_for_verdict())
        await c.worker.queue_frame(CancelFrame())
        assert (await asyncio.wait_for(pending, 1)).action == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("screening", [False, True])
async def test_shutdown_cancels_managed_classifier_and_waits_for_cleanup(screening):
    entered, cleaned_up = asyncio.Event(), asyncio.Event()

    async def classify(_):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned_up.set()

    async with call(
        classify=classify, classify_budget_ms=1000, screening_wait_ms=40
    ) as c:
        task_manager = c.supervisor.task_manager
        task_prefix = f"{c.supervisor}::"
        assert any(
            task.get_name() == f"{task_prefix}_listening_timeout"
            for task in task_manager.current_tasks()
        )
        if screening:
            c.supervisor.begin_screening_wait()
        await c.say("A long ambiguous answer", duration=0.07)
        await asyncio.wait_for(entered.wait(), 1)
        assert any(
            task.get_name() == f"{task_prefix}_classify_turn"
            for task in task_manager.current_tasks()
        )
        await c.worker.queue_frame(CancelFrame())
        assert (await verdict(c)).action == "cancelled"

    assert cleaned_up.is_set()
    assert not any(
        task.get_name().startswith(task_prefix) for task in task_manager.current_tasks()
    )
