"""Late answers through the real split pipeline, playback and transcript lifecycle."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import (
    CancelFrame,
    InterruptionFrame,
    LLMContextFrame,
    ProposedUserStartedSpeakingFrame,
    ProposedUserStoppedSpeakingFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.tests.mock_transport import MockOutputTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.turns.user_start import ExternalUserTurnStartStrategy
from pipecat.turns.user_stop import ExternalUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from api.schemas.answer_supervisor import AnswerSupervisorConfig
from api.services.pipecat.agent_bridge import AgentBridgeProcessor
from api.services.pipecat.answer_classification import MachineSubtype
from api.services.pipecat.in_memory_buffers import InMemoryLogsBuffer
from api.services.pipecat.pipeline_builder import create_agent_worker
from api.services.pipecat.processors.answer_supervisor import AnswerSupervisor
from api.services.pipecat.realtime_feedback_observer import register_turn_log_handlers
from api.services.pipecat.run_pipeline import _create_user_mute_strategies
from api.services.pipecat.speech_playback import PlaybackOutcome
from api.services.pipecat.transcript_log_coordinator import TranscriptLogCoordinator
from api.services.pipecat.worker_runner import create_worker_runner, run_worker_runner
from api.services.workflow.pipecat_engine import PipecatEngine
from pipecat.tests import MockLLMService, MockTTSService


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


class HeldOutput(MockOutputTransport):
    """Pause the opening in the output queue until completion or interruption."""

    def __init__(self):
        super().__init__(
            TransportParams(audio_out_enabled=True, audio_out_sample_rate=16000)
        )
        self.writing = asyncio.Event()
        self.resume = asyncio.Event()
        self.interruptions = 0

    async def write_audio_frame(self, frame):
        self.writing.set()
        await self.resume.wait()
        return await super().write_audio_frame(frame)

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, InterruptionFrame):
            self.interruptions += 1
            self.resume.set()


@asynccontextmanager
async def late_call(
    workflow,
    greeting_type="text",
    *,
    allow_interrupt=False,
    classify=None,
    messages=None,
    idle_timeout=10,
    **config,
):
    node = workflow.nodes[workflow.start_node_id]
    node.greeting_type = greeting_type
    node.greeting = "Welcome." if greeting_type == "text" else None
    node.greeting_recording_id = "9" if greeting_type == "audio" else None
    node.allow_interrupt = allow_interrupt
    context = LLMContext(messages=messages)
    llm = MockLLMService(
        mock_steps=[
            MockLLMService.create_text_chunks(text)
            for text in (
                (["Welcome."] if greeting_type == "llm" else []) + ["How can I help?"]
            )
        ],
        chunk_delay=0.001,
    )
    generation_contexts = []
    generation_messages = []

    @llm.event_handler("on_before_process_frame")
    async def generated(_processor, frame):
        if isinstance(frame, LLMContextFrame):
            generation_contexts.append(frame.context)
            generation_messages.append(deepcopy(frame.context.messages))

    tts = MockTTSService(mock_audio_duration_ms=120, frame_delay=0)
    output = HeldOutput()
    engine = PipecatEngine(
        llm=llm,
        workflow=workflow,
        context=context,
        call_context_vars={},
        workflow_run_id=1,
    )
    engine.active_agent.current_node = node
    engine.active_agent.is_child = True
    engine.set_transport_output(output)
    engine.set_fetch_recording_audio(
        AsyncMock(
            return_value=SimpleNamespace(
                audio=b"\x01\x00" * 1920, transcript="Welcome."
            )
        )
    )
    engine.end_call_with_reason = AsyncMock()
    supervisor = AnswerSupervisor(
        AnswerSupervisorConfig(
            **{
                "listening_window_ms": 10,
                "human_utterance_max_ms": 100,
                "machine_utterance_cap_ms": 1000,
                "classify_budget_ms": 500,
                "screening_wait_ms": 500,
                **config,
            }
        ),
        context=context,
        classify=classify,
    )
    pair = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                start=[ExternalUserTurnStartStrategy()],
                stop=[ExternalUserTurnStopStrategy()],
            ),
            user_mute_strategies=_create_user_mute_strategies(engine, supervisor),
            should_interrupt=engine.should_interrupt_user_turn,
        ),
    )
    supervisor.bind(pair.user())
    user_starts = []
    idle_events = []
    idle_handler = engine.create_user_idle_handler()

    @pair.user().event_handler("on_user_turn_started")
    async def user_started(_aggregator, strategy):
        user_starts.append(strategy)
        idle_handler.reset()

    @pair.user().event_handler("on_user_turn_idle")
    async def user_idle(aggregator):
        idle_events.append(asyncio.get_running_loop().time())
        await idle_handler.handle_idle(aggregator)

    engine.set_answer_supervisor(supervisor, pair.user(), idle_timeout)
    runner = create_worker_runner()
    bridge = AgentBridgeProcessor(
        bus=runner.bus,
        worker_name="call",
        selected_visit=lambda: "agent",
        allow_inference=lambda: True,
    )
    worker = PipelineWorker(
        Pipeline(
            [
                supervisor,
                pair.user(),
                supervisor.llm_gate(),
                bridge,
                output,
                pair.assistant(),
            ]
        ),
        name="call",
        enable_rtvi=False,
    )
    engine.call_worker = worker
    child = create_agent_worker(Pipeline([llm, tts]), "agent", call_worker_name="call")
    engine.active_agent.worker = child
    logs = InMemoryLogsBuffer(workflow_run_id=1)
    coordinator = TranscriptLogCoordinator(logs)
    coordinator.attach_turn_tracking_observer(worker.turn_tracking_observer)
    register_turn_log_handlers(coordinator, pair.user(), pair.assistant())
    turns = []

    @worker.turn_tracking_observer.event_handler("on_turn_ended")
    async def ended(_observer, turn, _duration, interrupted):
        turns.append((turn, interrupted))

    run = asyncio.create_task(run_worker_runner(runner, worker))
    action = None
    try:
        await until(lambda: worker.started_at is not None)
        await worker.add_workers(child)
        await until(lambda: child.started_at is not None)
        await worker.activate_worker("agent")
        await until(lambda: child.active and child.started_at is not None)
        supervisor.arm()
        action = asyncio.create_task(engine.handle_answer_supervision())
        await asyncio.wait_for(output.writing.wait(), 3)
        speech = next(iter(engine.speech_playback.pending.values()))

        async def start():
            starts = len(user_starts)
            await worker.queue_frame(ProposedUserStartedSpeakingFrame())
            await until(lambda: len(user_starts) > starts)

        async def stop(text):
            await worker.queue_frame(
                TranscriptionFrame(text, "caller", "", finalized=True)
            )
            await worker.queue_frame(ProposedUserStoppedSpeakingFrame())

        async def say(text):
            await start()
            await stop(text)

        async def persisted():
            await worker.wait_for_observers()
            await coordinator.flush()
            return logs.get_events()

        yield SimpleNamespace(
            engine=engine,
            supervisor=supervisor,
            worker=worker,
            output=output,
            llm=llm,
            speech=speech,
            action=action,
            say=say,
            start=start,
            stop=stop,
            context=context,
            persisted=persisted,
            turns=turns,
            generation_contexts=generation_contexts,
            generation_messages=generation_messages,
            idle_events=idle_events,
        )
    finally:
        output.resume.set()
        if action:
            action.cancel()
            await asyncio.gather(action, return_exceptions=True)
        if not child.has_finished():
            await child.cancel()
        if not run.done():
            await worker.queue_frame(CancelFrame())
        await asyncio.wait_for(run, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("greeting_type", ["text", "audio", "llm"])
@pytest.mark.parametrize("allow_interrupt", [False, True])
async def test_human_during_greeting_is_logged_without_an_immediate_reply(
    simple_workflow, greeting_type, allow_interrupt
):
    async with late_call(
        simple_workflow, greeting_type, allow_interrupt=allow_interrupt
    ) as c:
        await c.say("Hello, can you help?")
        await until(lambda: len(c.engine._gathered_context["answer_supervisor"]) == 2)
        assert not c.speech.done
        assert not c.action.done()
        assert c.supervisor.blocks_workflow
        assert c.output.interruptions == 0
        assert c.llm.get_current_step() == (1 if greeting_type == "llm" else 0)
        c.output.resume.set()
        await asyncio.wait_for(c.action, 3)
        assert await c.engine.drain_call_pipeline()
        assert c.speech.outcome == PlaybackOutcome.PLAYED
        assert c.output.interruptions == 0
        assert c.llm.get_current_step() == (1 if greeting_type == "llm" else 0)
        if greeting_type == "llm":
            assert c.generation_contexts[0] is not c.context
            assert not any(
                m.get("role") == "user" for m in c.generation_contexts[0].messages
            )
        assert not c.supervisor.blocks_workflow
        events = await c.persisted()
        users = [e for e in events if e["type"] == "rtf-user-transcription"]
        assert [e["payload"]["text"] for e in users] == ["Hello, can you help?"]
        assert users[0]["payload"]["end_timestamp"]
        assert not any(interrupted for _, interrupted in c.turns)
        await c.say("I need to change my appointment.")
        await until(
            lambda: c.llm.get_current_step() == (2 if greeting_type == "llm" else 1)
        )
        assert await c.engine.drain_call_pipeline()
        assert c.generation_messages[-1] == [
            {"role": "user", "content": "Hello, can you help?"},
            {"role": "assistant", "content": "Welcome."},
            {"role": "user", "content": "I need to change my appointment."},
        ]
        c.engine.end_call_with_reason.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_interrupt", [False, True])
async def test_multiple_turns_during_greeting_do_not_trigger_a_reply(
    simple_workflow, allow_interrupt
):
    history = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Earlier caller message"},
        {"role": "assistant", "content": "Earlier response"},
    ]
    async with late_call(
        simple_workflow, allow_interrupt=allow_interrupt, messages=list(history)
    ) as c:
        await c.say("Hello?")
        await until(lambda: len(c.engine._gathered_context["answer_supervisor"]) == 2)
        await c.say("I need to change my appointment.")
        await until(lambda: c.supervisor.llm_gate().dropped_contexts == 2)
        assert not c.speech.done
        assert not c.action.done()
        assert c.output.interruptions == 0
        assert c.llm.get_current_step() == 0
        c.output.resume.set()
        await asyncio.wait_for(c.action, 3)
        assert await c.engine.drain_call_pipeline()
        assert c.speech.outcome == PlaybackOutcome.PLAYED
        assert c.llm.get_current_step() == 0
        assert c.context.messages == [
            *history,
            {"role": "user", "content": "Hello?"},
            {"role": "user", "content": "I need to change my appointment."},
            {"role": "assistant", "content": "Welcome."},
        ]
        events = await c.persisted()
        assert [
            e["payload"]["text"]
            for e in events
            if e["type"] == "rtf-user-transcription"
        ] == ["Hello?\nI need to change my appointment."]
        assert c.engine.should_interrupt_user_turn()
        c.engine.end_call_with_reason.assert_not_awaited()


@pytest.mark.asyncio
async def test_delayed_human_classification_does_not_replay_a_greeting_turn(
    simple_workflow,
):
    result = asyncio.Event()

    async def classify(text):
        await result.wait()
        return MachineSubtype.CONVERSATION

    classifier = AsyncMock(side_effect=classify)
    async with late_call(
        simple_workflow, classify=classifier, human_utterance_max_ms=1
    ) as c:
        await c.say("I was expecting your call.")
        await until(lambda: classifier.await_count == 1)
        c.output.resume.set()
        await until(lambda: c.speech.done)
        assert await c.engine.drain_call_pipeline()
        assert not c.action.done()
        result.set()
        await asyncio.wait_for(c.action, 3)
        assert await c.engine.drain_call_pipeline()
        assert c.llm.get_current_step() == 0
        assert not c.supervisor.blocks_workflow
        assert c.output.interruptions == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("starts_during_greeting", [False, True])
async def test_turn_completing_after_greeting_is_answered(
    simple_workflow, starts_during_greeting
):
    async with late_call(simple_workflow) as c:
        if starts_during_greeting:
            await c.start()
        c.output.resume.set()
        await until(lambda: c.speech.done)
        assert await c.engine.drain_call_pipeline()
        if not starts_during_greeting:
            await asyncio.wait_for(c.action, 3)
            assert not c.supervisor.blocks_workflow
            await c.start()
        await c.stop("Can we reschedule?")
        await asyncio.wait_for(c.action, 3)
        assert await c.engine.drain_call_pipeline()
        assert c.llm.get_current_step() == 1
        assert c.generation_messages[0][-1] == {
            "role": "user",
            "content": "Can we reschedule?",
        }
        assert not c.supervisor.blocks_workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("greeting_type", ["text", "audio", "llm"])
async def test_idle_timeout_resumes_after_consuming_greeting_speech(
    simple_workflow, greeting_type
):
    async with late_call(simple_workflow, greeting_type, idle_timeout=0.2) as c:
        await c.say("Hello?")
        await until(lambda: len(c.engine._gathered_context["answer_supervisor"]) == 2)
        await asyncio.sleep(0.25)
        assert not c.idle_events
        assert c.llm.get_current_step() == (1 if greeting_type == "llm" else 0)
        c.output.resume.set()
        await asyncio.wait_for(c.action, 3)
        assert await c.engine.drain_call_pipeline()
        assert not c.idle_events
        assert c.llm.get_current_step() == (1 if greeting_type == "llm" else 0)
        await until(lambda: len(c.idle_events) == 1)
        await until(
            lambda: c.llm.get_current_step() == (2 if greeting_type == "llm" else 1)
        )
        assert await c.engine.drain_call_pipeline()
        assert c.context.messages[-2]["content"].startswith("The user has been quiet.")
        c.engine.end_call_with_reason.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("greeting_type", ["text", "audio", "llm"])
@pytest.mark.parametrize("leave_message", [False, True])
async def test_late_voicemail_stops_opening_and_applies_policy(
    simple_workflow, greeting_type, leave_message
):
    async with late_call(
        simple_workflow,
        greeting_type,
        voicemail_action="leave_message" if leave_message else "hangup",
        voicemail_message={"text": "Please call us back."},
    ) as c:
        await c.say("Please leave a message after the tone.")
        await asyncio.wait_for(c.action, 3)
        assert c.speech.outcome == PlaybackOutcome.INTERRUPTED
        assert c.output.interruptions == 1
        c.engine.end_call_with_reason.assert_awaited_once_with(
            "voicemail_detected", abort_immediately=True
        )
        assert c.llm.get_current_step() == (1 if greeting_type == "llm" else 0)
        assert not c.engine.speech_playback.pending
        events = await c.persisted()
        assert [
            e["payload"]["text"]
            for e in events
            if e["type"] == "rtf-user-transcription"
        ] == ["Please leave a message after the tone."]
        if leave_message:
            messages = [
                e for e in events if e["payload"].get("text") == "Please call us back."
            ]
            assert len(messages) == 1, events
            assert messages[0]["turn"] == 2


@pytest.mark.asyncio
async def test_opening_end_does_not_cut_off_active_speech_or_classifier(
    simple_workflow,
):
    result = asyncio.Event()

    async def classify(text):
        await result.wait()
        return MachineSubtype.VOICEMAIL

    classifier = AsyncMock(side_effect=classify)
    async with late_call(
        simple_workflow, classify=classifier, human_utterance_max_ms=1
    ) as c:
        await c.start()
        c.output.resume.set()
        await until(lambda: c.speech.done)
        await asyncio.sleep(0.08)
        assert not c.action.done()
        await c.stop("An ambiguous recorded announcement")
        await until(lambda: classifier.await_count == 1)
        await asyncio.sleep(0.08)
        assert not c.action.done()
        result.set()
        await asyncio.wait_for(c.action, 3)
        c.engine.end_call_with_reason.assert_awaited_once_with(
            "voicemail_detected", abort_immediately=True
        )
        events = await c.persisted()
        assert [
            e["payload"]["text"]
            for e in events
            if e["type"] == "rtf-user-transcription"
        ] == ["An ambiguous recorded announcement"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "announcement",
    ["Tell me your name and reason for calling.", "Please stay on the line."],
)
async def test_late_screening_stops_greeting_then_hands_over_once(
    simple_workflow, announcement
):
    async with late_call(
        simple_workflow, screening_message={"text": "Alex calling."}
    ) as c:
        await c.say(announcement)
        await until(lambda: c.supervisor._screening)
        assert c.speech.outcome == PlaybackOutcome.INTERRUPTED
        assert c.output.interruptions == 1
        await c.say("Hello, this is Alex.")
        await asyncio.wait_for(c.action, 3)
        assert await c.engine.drain_call_pipeline()
        assert c.llm.get_current_step() == 1
        assert [
            m["content"] for m in c.context.messages if m.get("role") == "user"
        ] == ["Hello, this is Alex."]
        assert not c.supervisor.blocks_workflow
        c.engine.end_call_with_reason.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_untranscribed_speech_is_bounded(simple_workflow):
    async with late_call(simple_workflow, machine_utterance_cap_ms=150) as c:
        await c.start()
        c.output.resume.set()
        await asyncio.wait_for(c.action, 3)
        c.engine.end_call_with_reason.assert_awaited_once_with(
            "machine_timeout", abort_immediately=True
        )


@pytest.mark.asyncio
async def test_speech_after_opening_is_normal_conversation(simple_workflow):
    async with late_call(simple_workflow) as c:
        c.output.resume.set()
        await asyncio.wait_for(c.action, 3)
        assert not c.supervisor.blocks_workflow
        await c.say("Please leave a message after the tone.")
        await until(lambda: c.llm.get_current_step() == 1)
        assert await c.engine.drain_call_pipeline()
        assert len(c.engine._gathered_context["answer_supervisor"]) == 2
        c.engine.end_call_with_reason.assert_not_awaited()


@pytest.mark.asyncio
async def test_silence_after_opening_finishes_without_a_human_verdict(simple_workflow):
    async with late_call(simple_workflow) as c:
        c.output.resume.set()
        await asyncio.wait_for(c.action, 3)
        history = c.engine._gathered_context["answer_supervisor"]
        assert [v["reason"] for v in history] == [
            "silent_window",
            "opening_complete",
        ]
        assert all(v["subtype"] is None for v in history)
        assert c.llm.get_current_step() == 0
        assert not c.supervisor.blocks_workflow


@pytest.mark.asyncio
async def test_disconnect_during_provisional_playback_cancels_supervision(
    simple_workflow,
):
    async with late_call(simple_workflow) as c:
        await c.worker.queue_frame(CancelFrame())
        await asyncio.wait_for(c.action, 3)
        assert not c.engine.speech_playback.pending
        c.engine.end_call_with_reason.assert_not_awaited()
