"""Late answers through the real split pipeline, playback and transcript lifecycle."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import (
    CancelFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMAssistantPushAggregationFrame,
    LLMContextFrame,
    ProposedUserStartedSpeakingFrame,
    ProposedUserStoppedSpeakingFrame,
    TranscriptionFrame,
    TTSTextFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
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
from pipecat.turns.user_stop import (
    ExternalUserTurnStopStrategy,
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from api.schemas.answer_supervisor import AnswerSupervisorConfig
from api.services.pipecat.agent_bridge import AgentBridgeProcessor
from api.services.pipecat.answer_classification import MachineSubtype
from api.services.pipecat.in_memory_buffers import InMemoryLogsBuffer
from api.services.pipecat.pipeline_builder import create_agent_worker
from api.services.pipecat.processors.answer_supervisor import AnswerSupervisor
from api.services.pipecat.realtime_feedback_observer import (
    RealtimeFeedbackObserver,
    register_turn_log_handlers,
)
from api.services.pipecat.run_pipeline import (
    _create_non_realtime_user_turn_start_strategies,
    _create_user_mute_strategies,
)
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
    start_with_screening=False,
    start_with_human=False,
    external_turns=True,
    min_words=None,
    supervised=True,
    turn_stop_timeout=5,
    recording_fetch=None,
    wait_for_output=True,
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
        recording_fetch
        or AsyncMock(
            return_value=SimpleNamespace(
                audio=b"\x01\x00" * 1920, transcript="Welcome."
            )
        )
    )
    engine.end_call_with_reason = AsyncMock()
    supervisor = AnswerSupervisor(
        AnswerSupervisorConfig(
            **{
                "listening_window_ms": 500
                if start_with_screening or start_with_human
                else 10,
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
    user, assistant = LLMContextAggregatorPair(
        context,
        realtime_service_mode=False,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                start=_create_non_realtime_user_turn_start_strategies(
                    {
                        "turn_start_strategy": "min_words",
                        "turn_start_min_words": min_words,
                    }
                    if min_words
                    else {},
                    uses_external_turns=external_turns,
                ),
                stop=[ExternalUserTurnStopStrategy()]
                if external_turns
                else [SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.01)],
            ),
            user_mute_strategies=_create_user_mute_strategies(engine, supervisor),
            should_interrupt=engine.should_interrupt_user_turn,
            user_turn_stop_timeout=turn_stop_timeout,
        ),
    )
    normal_strategies = user.user_turn_controller.user_turn_strategies
    engine.greeting.bind(user)
    if supervised:
        supervisor.bind(user)
    user_starts = []
    processed_onsets = []
    processed_transcripts = []

    @user.event_handler("on_after_process_frame")
    async def onset_processed(_user, frame):
        if isinstance(
            frame, (ProposedUserStartedSpeakingFrame, VADUserStartedSpeakingFrame)
        ):
            processed_onsets.append(frame)
        elif isinstance(frame, TranscriptionFrame):
            processed_transcripts.append(frame)

    idle_events = []
    engine.call_monitor.bind_user(user, idle_timeout=idle_timeout)

    @user.event_handler("on_user_turn_started")
    async def user_started(_aggregator, strategy):
        user_starts.append(strategy)

    async def user_idle(attempt):
        idle_events.append(asyncio.get_running_loop().time())
        await engine._on_user_idle(attempt)

    engine.call_monitor._on_user_idle = user_idle

    if supervised:
        engine.set_answer_supervisor(supervisor, user, idle_timeout)
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
                *([supervisor] if supervised else []),
                user,
                *([supervisor.llm_gate()] if supervised else []),
                engine.call_monitor,
                bridge,
                output,
                assistant,
            ]
        ),
        name="call",
        enable_rtvi=False,
    )
    engine.call_worker = worker
    logs = InMemoryLogsBuffer(workflow_run_id=1)
    feedback = RealtimeFeedbackObserver(
        ws_sender=AsyncMock(), logs_buffer=logs, selected_visit=lambda: "agent"
    )
    worker.add_observer(feedback)
    engine.greeting.log_generated_speech = feedback.log_speech
    child = create_agent_worker(
        Pipeline([llm, tts]),
        "agent",
        call_worker_name="call",
        observers=[feedback],
    )
    engine.active_agent.worker = child
    coordinator = TranscriptLogCoordinator(logs)
    coordinator.attach_turn_tracking_observer(worker.turn_tracking_observer)
    register_turn_log_handlers(coordinator, user, assistant)
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
        if supervised:
            supervisor.arm()
            action = asyncio.create_task(engine.handle_answer_supervision())
        else:
            engine.call_monitor.activate()
            action = asyncio.create_task(
                engine.queue_node_opening(
                    node_id=workflow.start_node_id,
                    generate_if_no_greeting=True,
                    wait_for_playback=True,
                )
            )

        async def start():
            starts = len(processed_onsets)
            await worker.queue_frame(
                ProposedUserStartedSpeakingFrame()
                if external_turns
                else VADUserStartedSpeakingFrame()
            )
            await until(lambda: len(processed_onsets) > starts)

        async def stop(text):
            transcript = TranscriptionFrame(text, "caller", "", finalized=True)
            await worker.queue_frame(transcript)
            await worker.queue_frame(
                ProposedUserStoppedSpeakingFrame()
                if external_turns
                else VADUserStoppedSpeakingFrame()
            )

            await until(lambda: transcript in processed_transcripts)

        async def say(text):
            await start()
            await stop(text)

        async def partial(text):
            processed = asyncio.Event()

            async def complete(*_):
                processed.set()

            await user.queue_frame(
                InterimTranscriptionFrame(text, "caller", ""), callback=complete
            )
            await asyncio.wait_for(processed.wait(), 1)

        async def persisted():
            await worker.wait_for_observers()
            await coordinator.flush()
            return logs.get_events()

        if start_with_screening:
            await say("Tell me your name and reason for calling.")
        elif start_with_human:
            await say("Hello.")
        if wait_for_output:
            await asyncio.wait_for(output.writing.wait(), 3)
        speech = next(iter(engine.speech_playback.pending.values()), None)

        yield SimpleNamespace(
            engine=engine,
            user=user,
            normal_strategies=normal_strategies,
            assistant=assistant,
            supervisor=supervisor,
            worker=worker,
            output=output,
            llm=llm,
            speech=speech,
            action=action,
            say=say,
            start=start,
            stop=stop,
            partial=partial,
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
@pytest.mark.parametrize("external_turns", [False, True])
@pytest.mark.parametrize("interrupt_during_fetch", [False, True])
async def test_speech_starting_during_recording_fetch_interrupts_at_two_words(
    simple_workflow, external_turns, interrupt_during_fetch
):
    fetching = asyncio.Event()
    fetched = asyncio.Event()

    async def fetch(**_):
        fetching.set()
        await fetched.wait()
        return SimpleNamespace(audio=b"\x01\x00" * 1920, transcript="Welcome.")

    async with late_call(
        simple_workflow,
        "audio",
        recording_fetch=AsyncMock(side_effect=fetch),
        wait_for_output=False,
        external_turns=external_turns,
        human_utterance_max_ms=1000,
    ) as c:
        await asyncio.wait_for(fetching.wait(), 1)
        await c.start()
        await c.partial("Hello")
        assert not c.user.user_turn_controller.has_active_user_turn
        assert c.output.interruptions == 0
        speech = c.engine.speech_playback.greeting
        assert speech is not None
        assert not speech.done

        if not interrupt_during_fetch:
            fetched.set()
            await asyncio.wait_for(c.output.writing.wait(), 1)
        await c.partial("Hello there")
        await until(lambda: speech.done)
        assert speech.outcome is PlaybackOutcome.INTERRUPTED
        await until(lambda: c.output.interruptions == 1)
        fetched.set()
        await c.stop("Hello there, can you help?")
        await asyncio.wait_for(c.action, 3)
        await until(lambda: c.llm.get_current_step() == 1)
        assert await c.engine.drain_call_pipeline()
        # A cancelled fetch must never enqueue the greeting after interruption.
        assert speech.started is not interrupt_during_fetch
        assert [
            m["content"] for m in c.generation_messages[-1] if m.get("role") == "user"
        ] == ["Hello there, can you help?"]
        assert not any(m.get("role") == "assistant" for m in c.generation_messages[-1])
        assert c.user.user_turn_controller.user_turn_strategies is c.normal_strategies
        if interrupt_during_fetch:
            events = await c.persisted()
            assert "Welcome." not in [
                e["payload"]["text"] for e in events if e["type"] == "rtf-bot-text"
            ]


@pytest.mark.asyncio
@pytest.mark.parametrize("external_turns", [False, True])
async def test_one_word_during_recording_fetch_leaves_greeting_intact(
    simple_workflow, external_turns
):
    fetching = asyncio.Event()
    fetched = asyncio.Event()

    async def fetch(**_):
        fetching.set()
        await fetched.wait()
        return SimpleNamespace(audio=b"\x01\x00" * 1920, transcript="Welcome.")

    async with late_call(
        simple_workflow,
        "audio",
        recording_fetch=AsyncMock(side_effect=fetch),
        wait_for_output=False,
        external_turns=external_turns,
    ) as c:
        await asyncio.wait_for(fetching.wait(), 1)
        await c.say("Hello.")
        assert c.output.interruptions == 0
        speech = c.engine.speech_playback.greeting
        assert speech is not None
        assert not speech.done
        fetched.set()
        c.output.resume.set()
        await asyncio.wait_for(c.action, 3)
        assert await c.engine.drain_call_pipeline()
        assert speech.outcome is PlaybackOutcome.PLAYED
        assert c.context.messages == [{"role": "assistant", "content": "Welcome."}]
        assert c.llm.get_current_step() == 0
        assert c.user.user_turn_controller.user_turn_strategies is c.normal_strategies
        await c.say("Hello.")
        await until(lambda: c.llm.get_current_step() == 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("greeting_type", ["text", "audio", "llm"])
@pytest.mark.parametrize("turns", ["external", "vad", "min_words"])
@pytest.mark.parametrize("supervised", [False, True])
async def test_second_partial_word_interrupts_greeting_and_answers_finished_turn(
    simple_workflow, greeting_type, turns, supervised
):
    async with late_call(
        simple_workflow,
        greeting_type,
        external_turns=turns == "external",
        min_words=3 if turns == "min_words" else None,
        supervised=supervised,
        human_utterance_max_ms=1000,
    ) as c:
        await c.start()
        for _ in range(2):
            await c.partial("Hello")
        assert c.output.interruptions == 0
        assert not c.speech.done
        active = c.user.user_turn_controller.user_turn_strategies
        assert active is not c.normal_strategies
        assert active.stop == c.normal_strategies.stop
        await c.partial("Hello there")
        await until(lambda: c.speech.done)
        assert c.speech.outcome is PlaybackOutcome.INTERRUPTED
        await until(
            lambda: (
                c.user.user_turn_controller.user_turn_strategies is c.normal_strategies
            )
        )
        assert c.output.interruptions == 1
        # Interrupting audio does not authorize inference from partial text.
        assert c.llm.get_current_step() == (1 if greeting_type == "llm" else 0)
        await c.stop("Hello there, can you help?")
        await asyncio.wait_for(c.action, 3)
        await until(
            lambda: c.llm.get_current_step() == (2 if greeting_type == "llm" else 1)
        )
        assert await c.engine.drain_call_pipeline()
        assert c.output.interruptions == 1
        assert [
            m["content"] for m in c.generation_messages[-1] if m.get("role") == "user"
        ] == ["Hello there, can you help?"]
        assert not any(m.get("role") == "assistant" for m in c.generation_messages[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize("greeting_type", ["text", "audio"])
@pytest.mark.parametrize("turns", ["external", "vad", "min_words"])
@pytest.mark.parametrize("supervised", [False, True])
async def test_separate_one_word_turns_are_discarded_without_inference(
    simple_workflow, greeting_type, turns, supervised
):
    async with late_call(
        simple_workflow,
        greeting_type,
        external_turns=turns == "external",
        min_words=3 if turns == "min_words" else None,
        supervised=supervised,
    ) as c:
        for word in ("Hello.", "Pronto?"):
            await c.say(word)
            assert not any(m.get("content") == word for m in c.context.messages)
            await until(lambda: not c.user._user_turn_controller.has_active_user_turn)
        assert not c.speech.done
        assert c.output.interruptions == 0
        assert c.llm.get_current_step() == 0
        c.output.resume.set()
        await asyncio.wait_for(c.action, 3)
        assert await c.engine.drain_call_pipeline()
        assert c.llm.get_current_step() == 0
        # The normal configuration applies again after the greeting.
        await c.worker.queue_frames(
            [
                ProposedUserStartedSpeakingFrame()
                if turns == "external"
                else VADUserStartedSpeakingFrame(),
                TranscriptionFrame("Can you help me?", "caller", "", finalized=True),
                ProposedUserStoppedSpeakingFrame()
                if turns == "external"
                else VADUserStoppedSpeakingFrame(),
            ]
        )
        await until(lambda: c.llm.get_current_step() == 1)
        users = [
            m["content"] for m in c.generation_messages[-1] if m.get("role") == "user"
        ]
        assert users == ["Can you help me?"]


@pytest.mark.asyncio
@pytest.mark.parametrize("greeting_type", ["text", "audio", "llm"])
@pytest.mark.parametrize("allow_interrupt", [False, True])
async def test_one_word_greeting_interjection_is_discarded(
    simple_workflow, greeting_type, allow_interrupt
):
    async with late_call(
        simple_workflow, greeting_type, allow_interrupt=allow_interrupt
    ) as c:
        await c.say("Hello.")
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
        assert users == []
        assert not any(interrupted for _, interrupted in c.turns)
        await c.say("I need to change my appointment.")
        await until(
            lambda: c.llm.get_current_step() == (2 if greeting_type == "llm" else 1)
        )
        assert await c.engine.drain_call_pipeline()
        assert c.generation_messages[-1] == [
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
        await c.say("Pronto?")
        assert c.supervisor.llm_gate().dropped_contexts == 0
        assert not c.speech.done
        assert not c.action.done()
        assert c.output.interruptions == 0
        assert c.llm.get_current_step() == 0
        c.output.resume.set()
        await asyncio.wait_for(c.action, 3)
        assert await c.engine.drain_call_pipeline()
        assert c.speech.outcome == PlaybackOutcome.PLAYED
        assert c.llm.get_current_step() == 0
        expected_messages = [
            *history,
            {"role": "assistant", "content": "Welcome."},
        ]
        assert c.context.messages == expected_messages
        events = await c.persisted()
        assert [
            e["payload"]["text"]
            for e in events
            if e["type"] == "rtf-user-transcription"
        ] == []
        assert c.engine.should_interrupt_user_turn()
        await c.say("Tomorrow afternoon would work.")
        await until(lambda: c.llm.get_current_step() == 1)
        assert await c.engine.drain_call_pipeline()
        assert c.generation_messages[0] == [
            *expected_messages,
            {"role": "user", "content": "Tomorrow afternoon would work."},
        ]
        c.engine.end_call_with_reason.assert_not_awaited()


@pytest.mark.asyncio
async def test_delayed_human_classification_answers_the_interruption_once(
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
        await c.say("Hello there.")
        await until(lambda: classifier.await_count == 1)
        c.output.resume.set()
        await until(lambda: c.speech.done)
        assert await c.engine.drain_call_pipeline()
        assert not c.action.done()
        result.set()
        await asyncio.wait_for(c.action, 3)
        assert await c.engine.drain_call_pipeline()
        assert c.llm.get_current_step() == 1
        assert not c.supervisor.blocks_workflow
        assert c.output.interruptions == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("greeting_type", ["text", "audio", "llm"])
async def test_turn_spanning_greeting_is_saved_for_the_next_user_turn(
    simple_workflow, greeting_type
):
    async with late_call(simple_workflow, greeting_type) as c:
        await c.start()
        c.output.resume.set()
        await until(lambda: c.speech.done)
        assert await c.engine.drain_call_pipeline()
        await c.stop("Can we reschedule?")
        await asyncio.wait_for(c.action, 3)
        assert await c.engine.drain_call_pipeline()
        assert c.llm.get_current_step() == (1 if greeting_type == "llm" else 0)
        assert not c.supervisor.blocks_workflow
        assert c.context.messages == [
            {"role": "assistant", "content": "Welcome."},
            {"role": "user", "content": "Can we reschedule?"},
        ]
        await c.say("Tomorrow at 3 PM.")
        await until(
            lambda: c.llm.get_current_step() == (2 if greeting_type == "llm" else 1)
        )
        assert await c.engine.drain_call_pipeline()
        assert c.generation_messages[-1] == [
            {"role": "assistant", "content": "Welcome."},
            {"role": "user", "content": "Can we reschedule?"},
            {"role": "user", "content": "Tomorrow at 3 PM."},
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("greeting_type", ["text", "audio", "llm"])
async def test_idle_timeout_resumes_after_consuming_greeting_speech(
    simple_workflow, greeting_type
):
    async with late_call(simple_workflow, greeting_type, idle_timeout=0.2) as c:
        await c.say("Hello?")
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
async def test_late_voicemail_interrupts_opening_then_applies_policy(
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
        # The mocked hangup leaves the pipeline running; close it to flush final text.
        await c.worker.queue_frame(CancelFrame())
        await asyncio.wait_for(c.worker.wait(), 3)
        events = await c.persisted()
        assert [
            e["payload"]["text"]
            for e in events
            if e["type"] == "rtf-user-transcription"
        ] == ["Please leave a message after the tone."]
        if leave_message:
            messages = [e for e in events if e["type"] == "rtf-bot-text"]
            assert (
                sum(e["payload"]["text"] == "Please call us back." for e in messages)
                == 1
            )
            assert messages[-1]["payload"]["text"] == "Please call us back."


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
        await c.partial("An ambiguous")
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
@pytest.mark.parametrize("greeting_type", ["text", "audio", "llm"])
@pytest.mark.parametrize(
    "announcement",
    ["Tell me your name and reason for calling.", "Please stay on the line."],
)
async def test_late_screening_interrupts_greeting_then_hands_over_once(
    simple_workflow, greeting_type, announcement
):
    async with late_call(
        simple_workflow, greeting_type, screening_message={"text": "Alex calling."}
    ) as c:
        await c.say(announcement)
        await until(lambda: c.supervisor._screening)
        assert c.speech.outcome == PlaybackOutcome.INTERRUPTED
        assert await c.engine.drain_call_pipeline()
        assert c.output.interruptions == 1
        await c.say("Hello, this is Alex.")
        await asyncio.wait_for(c.action, 3)
        assert await c.engine.drain_call_pipeline()
        await until(
            lambda: c.llm.get_current_step() == (2 if greeting_type == "llm" else 1)
        )
        assert [
            m["content"] for m in c.generation_messages[-1] if m.get("role") == "user"
        ] == ["Hello, this is Alex."]
        assert [
            m["content"] for m in c.context.messages if m.get("role") == "user"
        ] == ["Hello, this is Alex."]
        assert not c.supervisor.blocks_workflow
        await c.say("Can we reschedule?")
        await until(
            lambda: c.llm.get_current_step() == (3 if greeting_type == "llm" else 2)
        )
        assert await c.engine.drain_call_pipeline()
        assert [
            m["content"] for m in c.generation_messages[-1] if m.get("role") == "user"
        ] == ["Hello, this is Alex.", "Can we reschedule?"]
        c.engine.end_call_with_reason.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "screening_message", [{"text": "Alex calling."}, {"recording_pk": 10}]
)
@pytest.mark.parametrize("finishes_after_playback", [False, True])
async def test_human_during_screening_reply_is_retained_and_releases_workflow(
    simple_workflow, screening_message, finishes_after_playback
):
    async with late_call(
        simple_workflow,
        screening_message=screening_message,
        screening_wait_ms=50,
        classify=AsyncMock(return_value=MachineSubtype.CONVERSATION),
    ) as c:
        c.engine.set_fetch_recording_audio(
            AsyncMock(
                return_value=SimpleNamespace(
                    audio=b"\x01\x00" * 1920, transcript="Alex calling."
                )
            )
        )
        screening_queued = asyncio.get_running_loop().create_future()
        queue_speech = c.engine.queue_speech

        async def queue_screening(**kwargs):
            c.output.resume.clear()
            c.output.writing.clear()
            speech = await queue_speech(**kwargs)
            screening_queued.set_result(speech)
            return speech

        c.engine.queue_speech = queue_screening
        await c.say("Tell me your name and reason for calling.")
        c.output.resume.set()
        speech = await asyncio.wait_for(screening_queued, 3)
        await asyncio.wait_for(c.output.writing.wait(), 3)
        assert not c.engine.speech_playback.mutes_user
        # Playback must not consume the subsequent silence budget.
        await asyncio.sleep(0.1)
        assert c.supervisor._verdict is None
        assert not speech.done

        await c.start()
        assert c.output.interruptions == 1
        if finishes_after_playback:
            c.output.resume.set()
            await until(lambda: speech.done)
            # Finishing playback must preserve an already-active caller turn.
            await asyncio.sleep(0.08)
            assert c.supervisor._verdict is None
        await c.stop("Hello, this is Alex.")
        await until(lambda: c.supervisor._verdict is not None)
        assert c.supervisor._verdict.subtype == MachineSubtype.CONVERSATION

        await asyncio.wait_for(c.action, 3)
        assert await c.engine.drain_call_pipeline()
        assert speech.outcome == (
            PlaybackOutcome.PLAYED
            if finishes_after_playback
            else PlaybackOutcome.INTERRUPTED
        )
        assert c.output.interruptions == (1 if finishes_after_playback else 2)
        assert not c.supervisor.blocks_workflow
        await until(lambda: c.llm.get_current_step() == 1)
        assert all(
            "Alex calling." not in m.get("content", "")
            for m in c.generation_messages[-1]
            if m.get("role") == "assistant"
        )
        if finishes_after_playback:
            events = await c.persisted()
            assert [
                e["payload"]["text"] for e in events if e["type"] == "rtf-bot-text"
            ].count("Alex calling.") == 1
        assert [
            m["content"] for m in c.context.messages if m.get("role") == "user"
        ] == ["Hello, this is Alex."]
        await c.say("Can we reschedule?")
        await until(lambda: c.llm.get_current_step() == 2)
        assert await c.engine.drain_call_pipeline()
        assert [
            m["content"] for m in c.generation_messages[-1] if m.get("role") == "user"
        ] == ["Hello, this is Alex.", "Can we reschedule?"]
        c.engine.end_call_with_reason.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("greeting_type", ["text", "audio", "llm"])
@pytest.mark.parametrize(
    "screening_message", [{"text": "Alex calling."}, {"recording_pk": 10}]
)
async def test_human_interrupts_screening_reply_before_initial_greeting(
    simple_workflow, greeting_type, screening_message
):
    async with late_call(
        simple_workflow,
        greeting_type,
        start_with_screening=True,
        screening_message=screening_message,
    ) as c:
        assert not c.speech.done
        assert not c.output.resume.is_set()
        await c.say("Hello.")
        # Only interruption can release the held output and allow the greeting.
        await asyncio.wait_for(c.action, 3)
        assert await c.engine.drain_call_pipeline()
        assert c.speech.outcome == PlaybackOutcome.INTERRUPTED
        assert c.output.interruptions == 1
        assert not c.engine.speech_playback.pending
        assert not c.supervisor.blocks_workflow
        assert [
            m["content"] for m in c.context.messages if m.get("role") == "user"
        ] == ["Hello."]
        assert [
            m["content"] for m in c.context.messages if m.get("role") == "assistant"
        ].count("Welcome.") == 1
        assert c.llm.get_current_step() == (1 if greeting_type == "llm" else 0)
        assert [
            v["action"] for v in c.engine._gathered_context["answer_supervisor"]
        ] == ["screen_then_rearm", "release"]
        c.engine.end_call_with_reason.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "screening_message", [{"text": "Alex calling."}, {"recording_pk": 10}]
)
async def test_discarded_speech_during_screening_reply_resumes_silence_timeout(
    simple_workflow, screening_message
):
    async with late_call(
        simple_workflow,
        start_with_screening=True,
        screening_message=screening_message,
        external_turns=False,
        min_words=2,
        screening_wait_ms=50,
    ) as c:
        await c.start()
        assert not c.supervisor._screening_idle.is_set()
        await c.stop("Hello.")
        # The minimum-word strategy discards this without starting a logical
        # turn, so no turn-stop callback will resume the screening timeout.
        assert not c.supervisor._logical_turn
        assert c.supervisor._onset is None
        assert not c.user.aggregation_string()
        assert not c.speech.done

        c.output.resume.set()
        await asyncio.wait_for(c.action, 3)
        c.engine.end_call_with_reason.assert_awaited_once_with(
            "screening_timeout", abort_immediately=True
        )
        assert c.speech.outcome == PlaybackOutcome.PLAYED
        assert c.llm.get_current_step() == 0
        assert not any(m.get("role") == "user" for m in c.context.messages)


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
@pytest.mark.parametrize(
    "announcement",
    [None, "Hello."],
)
async def test_disconnect_during_provisional_playback_cancels_supervision(
    simple_workflow, announcement
):
    async with late_call(simple_workflow) as c:
        if announcement:
            await c.say(announcement)
            assert not c.action.done()
        await c.worker.queue_frame(CancelFrame())
        await asyncio.wait_for(c.action, 3)
        assert not c.engine.speech_playback.pending
        c.engine.end_call_with_reason.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("greeting_type", ["text", "audio"])
@pytest.mark.parametrize("start_with_human", [False, True])
@pytest.mark.parametrize("external_turns", [False, True])
async def test_two_word_final_also_interrupts_and_answers_once(
    simple_workflow, greeting_type, start_with_human, external_turns
):
    async with late_call(
        simple_workflow,
        greeting_type,
        start_with_human=start_with_human,
        external_turns=external_turns,
    ) as c:
        await c.say("Wait please.")
        await asyncio.wait_for(c.action, 3)
        await until(lambda: c.llm.get_current_step() == 1)
        assert await c.engine.drain_call_pipeline()
        assert c.speech.outcome is PlaybackOutcome.INTERRUPTED
        assert c.output.interruptions == 1
        assert [
            m["content"] for m in c.generation_messages[-1] if m.get("role") == "user"
        ][-1] == "Wait please."
        assert c.llm.get_current_step() == 1


@pytest.mark.asyncio
async def test_discarded_final_segment_does_not_count_toward_next_partial(
    simple_workflow,
):
    async with late_call(simple_workflow) as c:
        await c.start()
        processed = asyncio.Event()

        async def complete(*_):
            processed.set()

        await c.user.queue_frame(
            TranscriptionFrame("Hello", "caller", ""), callback=complete
        )
        await processed.wait()
        assert not c.speech.done
        await c.partial("there")
        assert not c.speech.done
        await c.partial("there please")
        await until(lambda: c.speech.done)
        assert c.output.interruptions == 1
        await c.stop("there please.")
        await asyncio.wait_for(c.action, 3)
        await until(lambda: c.llm.get_current_step() == 1)
        assert [
            m["content"] for m in c.generation_messages[-1] if m.get("role") == "user"
        ] == ["there please."]


@pytest.mark.asyncio
async def test_interrupted_greeting_waits_for_human_classification(simple_workflow):
    result = asyncio.Event()

    async def classify(_text):
        await result.wait()
        return MachineSubtype.CONVERSATION

    classifier = AsyncMock(side_effect=classify)
    async with late_call(
        simple_workflow,
        classify=classifier,
        human_utterance_max_ms=1,
    ) as c:
        await c.start()
        await c.partial("Wait please")
        await until(lambda: c.speech.done)
        await c.stop("Wait please.")
        await until(lambda: classifier.await_count == 1)
        assert c.output.interruptions == 1
        assert c.llm.get_current_step() == 0
        result.set()
        await asyncio.wait_for(c.action, 3)
        await until(lambda: c.llm.get_current_step() == 1)
        assert await c.engine.drain_call_pipeline()
        assert c.llm.get_current_step() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [PlaybackOutcome.FAILED, PlaybackOutcome.TIMED_OUT])
async def test_failed_greeting_restores_normal_strategy_without_committing_context(
    simple_workflow, outcome
):
    async with late_call(simple_workflow) as c:
        c.speech.finish(outcome)
        c.output.resume.set()
        await asyncio.wait_for(c.action, 3)
        assert await c.engine.drain_call_pipeline()
        assert c.user.user_turn_controller.user_turn_strategies is c.normal_strategies
        assert not any(m.get("role") == "assistant" for m in c.context.messages)
        await c.say("Hello.")
        await until(lambda: c.llm.get_current_step() == 1)


@pytest.mark.asyncio
async def test_later_playback_uses_normal_immediate_interruption(simple_workflow):
    async with late_call(simple_workflow, allow_interrupt=True) as c:
        c.output.resume.set()
        await asyncio.wait_for(c.action, 3)
        c.output.resume.clear()
        c.output.writing.clear()
        later = await c.engine.queue_speech(text="How can I help?")
        await asyncio.wait_for(c.output.writing.wait(), 3)
        await c.start()
        await until(lambda: later.done)
        assert later.outcome is PlaybackOutcome.INTERRUPTED
        assert c.speech.outcome is PlaybackOutcome.PLAYED
        await until(lambda: c.output.interruptions == 1)
        assert c.output.interruptions == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("greeting_type", ["text", "audio", "llm"])
@pytest.mark.parametrize("interrupt", [False, True])
@pytest.mark.parametrize("supervised", [False, True])
async def test_later_opening_uses_the_same_greeting_policy(
    simple_workflow, greeting_type, interrupt, supervised
):
    async with late_call(simple_workflow, supervised=supervised) as c:
        c.output.resume.set()
        await asyncio.wait_for(c.action, 3)
        assert await c.engine.drain_call_pipeline()
        assert c.speech.outcome is PlaybackOutcome.PLAYED

        node = c.engine.active_agent.current_node
        node.greeting_type = greeting_type
        node.greeting = "Billing here." if greeting_type == "text" else None
        node.greeting_recording_id = "10" if greeting_type == "audio" else None
        c.engine.set_fetch_recording_audio(
            AsyncMock(
                return_value=SimpleNamespace(
                    audio=b"\x01\x00" * 1920, transcript="Billing here."
                )
            )
        )
        c.llm.set_mock_steps(
            [
                MockLLMService.create_text_chunks(text)
                for text in (
                    (["Billing here."] if greeting_type == "llm" else [])
                    + ["Certainly."]
                )
            ]
        )
        c.output.resume.clear()
        c.output.writing.clear()
        # This is the same opening entry point used by an agent transfer.
        await c.engine.queue_node_opening(node_id=node.id, generate_if_no_greeting=True)
        await asyncio.wait_for(c.output.writing.wait(), 3)
        later = c.engine.speech_playback.greeting
        assert later is not c.speech

        await c.say("Hello.")
        assert not any(m.get("content") == "Hello." for m in c.context.messages)
        assert not later.done
        assert c.output.interruptions == 0
        opening_generations = 1 if greeting_type == "llm" else 0
        assert c.llm.get_current_step() == opening_generations

        if interrupt:
            await c.start()
            await c.partial("Wait please")
            await until(lambda: later.done)
            assert later.outcome is PlaybackOutcome.INTERRUPTED
            await c.stop("Wait please.")
        else:
            c.output.resume.set()
            assert await asyncio.wait_for(later.wait(), 3)
            assert await c.engine.drain_call_pipeline()
            assert c.llm.get_current_step() == opening_generations
            await c.say("Can you help?")

        await until(lambda: c.llm.get_current_step() == opening_generations + 1)
        assert await c.engine.drain_call_pipeline()
        assert [
            m["content"]
            for m in c.generation_messages[-1]
            if m.get("role") == "assistant"
        ] == (["Welcome."] if interrupt else ["Welcome.", "Billing here."])
        assert [
            m["content"] for m in c.generation_messages[-1] if m.get("role") == "user"
        ] == ["Wait please." if interrupt else "Can you help?"]


@pytest.mark.asyncio
async def test_disconnect_while_waiting_for_interrupting_turn(simple_workflow):
    async with late_call(simple_workflow, start_with_human=True) as c:
        await c.start()
        await c.partial("Wait please")
        await until(lambda: c.speech.done)
        assert not c.action.done()
        await c.worker.queue_frame(CancelFrame())
        await asyncio.wait_for(c.action, 3)
        assert c.llm.get_current_step() == 0


@pytest.mark.asyncio
async def test_missing_final_does_not_answer_the_pre_greeting_hello(simple_workflow):
    async with late_call(
        simple_workflow, start_with_human=True, turn_stop_timeout=0.05
    ) as c:
        await c.start()
        await c.partial("Wait please")
        await until(lambda: c.speech.done)
        await c.worker.queue_frame(ProposedUserStoppedSpeakingFrame())
        await asyncio.wait_for(c.action, 3)
        assert c.output.interruptions == 1
        assert c.llm.get_current_step() == 0
        assert not c.supervisor.blocks_workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt_greeting", [False, True])
async def test_human_after_screening_answers_when_recorded_greeting_already_ran(
    simple_workflow,
    interrupt_greeting,
):
    async with late_call(
        simple_workflow, "audio", screening_message={"text": "Alex calling."}
    ) as c:
        screening_queued = asyncio.get_running_loop().create_future()
        queue_speech = c.engine.queue_speech

        async def queue_screening(**kwargs):
            speech = await queue_speech(**kwargs)
            screening_queued.set_result(speech)
            return speech

        c.engine.queue_speech = queue_screening
        await c.start()
        if interrupt_greeting:
            await c.partial("Hi. If")
        else:
            c.output.resume.set()
        await until(lambda: c.speech.done)
        await c.stop(
            "Hi. If you record your name and reason for calling, "
            "I'll see if this person is available."
        )
        await until(lambda: c.supervisor._screening)
        screening = await asyncio.wait_for(screening_queued, 3)
        assert await asyncio.wait_for(screening.wait(), 3)
        assert await c.engine.drain_call_pipeline()
        assert not c.engine.speech_playback.pending
        assert c.speech.outcome is (
            PlaybackOutcome.INTERRUPTED
            if interrupt_greeting
            else PlaybackOutcome.PLAYED
        )
        assert c.llm.get_current_step() == 0

        await c.say("Hello. Yes yes ma'am.")
        await asyncio.wait_for(c.action, 3)
        assert await c.engine.drain_call_pipeline()
        assert c.llm.get_current_step() == 1
        assert not c.supervisor.blocks_workflow
        assert all(
            "Alex calling." not in m.get("content", "")
            for m in c.generation_messages[-1]
            if m.get("role") == "assistant"
        )
        assert [
            m["content"] for m in c.generation_messages[-1] if m.get("role") == "user"
        ] == ["Hello. Yes yes ma'am."]
        assert [
            m["content"]
            for m in c.generation_messages[-1]
            if m.get("role") == "assistant"
        ] == ([] if interrupt_greeting else ["Welcome."])


@pytest.mark.asyncio
@pytest.mark.parametrize("greeting_type", ["text", "audio", "llm"])
@pytest.mark.parametrize("flush_before_interruption", [False, True])
@pytest.mark.parametrize("supervised", [False, True])
async def test_interrupted_greeting_text_is_logged_but_excluded_from_inference(
    simple_workflow, greeting_type, flush_before_interruption, supervised
):
    history = [{"role": "assistant", "content": "Welcome."}]
    async with late_call(
        simple_workflow,
        greeting_type,
        supervised=supervised,
        messages=list(history),
    ) as c:
        if greeting_type != "audio":
            # A provider with word timestamps can deliver spoken text while
            # the output is still playing the rest of the greeting's audio.
            await c.output.push_frame(TTSTextFrame("Welcome.", aggregated_by="word"))
        await until(
            lambda: c.engine.speech_playback.greeting_text(c.speech) == "Welcome."
        )
        assert not c.assistant.aggregation_string()
        if flush_before_interruption:
            await c.output.push_frame(LLMAssistantPushAggregationFrame())
            await until(lambda: not c.assistant.aggregation_string())
        assert not c.speech.done
        assert c.context.messages == history

        await c.say("Wait please.")
        await asyncio.wait_for(c.action, 3)
        await until(
            lambda: c.llm.get_current_step() == (2 if greeting_type == "llm" else 1)
        )
        assert await c.engine.drain_call_pipeline()
        assert c.speech.outcome is PlaybackOutcome.INTERRUPTED
        # Preserve prior history even when its text matches the greeting.
        assert c.generation_messages[-1] == [
            *history,
            {"role": "user", "content": "Wait please."},
        ]
        events = await c.persisted()
        assert [
            e["payload"]["text"] for e in events if e["type"] == "rtf-bot-text"
        ].count("Welcome.") == 1
