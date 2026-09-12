"""
Simulates a realistic conversation and tests the user idle handler behavior.

This module tests the user idle handler in a natural back-and-forth conversation
where bot and user take turns speaking, verifying that:
1. The idle handler does not trigger while the bot is speaking (even when
   TTS duration exceeds the idle timeout)
2. User speech properly resets the idle timer
3. The conversation flows naturally through node transitions to completion
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    LLMMessagesAppendFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    UserSpeakingFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMAssistantAggregatorParams,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.tests.mock_transport import MockTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.turns.user_mute import (
    CallbackUserMuteStrategy,
    MuteUntilFirstBotCompleteUserMuteStrategy,
)
from pipecat.turns.user_start import TranscriptionUserTurnStartStrategy
from pipecat.turns.user_stop import ExternalUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.utils.enums import EndTaskReason
from pipecat.utils.time import time_now_iso8601

from api.services.pipecat.worker_runner import run_pipeline_worker
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.workflow_graph import WorkflowGraph
from api.tests.pipecat_test_utils import run_engine_test_pipeline
from pipecat.tests import MockLLMService, MockTTSService


class UserSpeechInjector(FrameProcessor):
    """Processor that injects user speaking frames after the bot finishes speaking.

    When this processor sees a BotStoppedSpeakingFrame flowing upstream,
    it injects UserStartedSpeakingFrame, TranscriptionFrame, and
    UserStoppedSpeakingFrame downstream to simulate user speech. Each
    BotStoppedSpeakingFrame triggers the next speech from the provided list.
    """

    def __init__(self, *, speeches: list[str], **kwargs):
        """Initialize the user speech injector.

        Args:
            speeches: List of transcription texts to inject, one per bot utterance.
            **kwargs: Additional arguments passed to parent class.
        """
        super().__init__(**kwargs)
        self._speeches = speeches
        self._bot_stopped_count = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_stopped_count += 1
            if self._bot_stopped_count <= len(self._speeches):
                speech_text = self._speeches[self._bot_stopped_count - 1]
                await asyncio.sleep(0.01)
                await self.push_frame(UserStartedSpeakingFrame())

                await asyncio.sleep(0)

                await self.broadcast_frame(UserSpeakingFrame)

                await asyncio.sleep(0)

                await self.push_frame(
                    TranscriptionFrame(speech_text, "user", time_now_iso8601())
                )

                await asyncio.sleep(0)
                await self.push_frame(UserStoppedSpeakingFrame())

        await self.push_frame(frame, direction)


async def create_pipeline_with_speech_injection(
    workflow: WorkflowGraph,
    mock_llm: MockLLMService,
    speeches: list[str],
    user_idle_timeout: float = 0.2,
    mock_audio_duration_ms: int = 400,
) -> tuple[PipecatEngine, MockTransport, PipelineWorker, object]:
    """Create a pipeline with user speech injection and idle handling.

    Sets up a realistic pipeline with:
    - MockTransport for audio I/O simulation
    - UserSpeechInjector that injects user speech after each bot utterance
    - User idle handler with configurable timeout
    - User turn and mute strategies matching production setup

    Args:
        workflow: The workflow graph to use.
        mock_llm: The mock LLM service with pre-configured steps.
        speeches: List of user speech texts to inject after each bot utterance.
        user_idle_timeout: Timeout in seconds for user idle detection.
        mock_audio_duration_ms: TTS audio duration in milliseconds.

    Returns:
        Tuple of (engine, transport, task, user_idle_handler).
    """
    tts = MockTTSService(
        mock_audio_duration_ms=mock_audio_duration_ms, frame_delay=0.001
    )

    transport = MockTransport(
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
            audio_out_end_silence_secs=0,
        ),
    )

    user_speech_injector = UserSpeechInjector(speeches=speeches)

    context = LLMContext()

    engine = PipecatEngine(
        llm=mock_llm,
        context=context,
        workflow=workflow,
        call_context_vars={"customer_name": "Test User"},
        workflow_run_id=1,
    )

    # User turn strategies matching production setup
    user_turn_strategies = UserTurnStrategies(
        start=[TranscriptionUserTurnStartStrategy()],
        stop=[ExternalUserTurnStopStrategy()],
    )

    user_mute_strategies = [
        MuteUntilFirstBotCompleteUserMuteStrategy(),
        CallbackUserMuteStrategy(should_mute_callback=engine.should_mute_user),
    ]

    user_params = LLMUserAggregatorParams(
        user_turn_strategies=user_turn_strategies,
        user_mute_strategies=user_mute_strategies,
        user_idle_timeout=user_idle_timeout,
    )

    assistant_params = LLMAssistantAggregatorParams()

    context_aggregator = LLMContextAggregatorPair(
        context, assistant_params=assistant_params, user_params=user_params
    )
    user_context_aggregator = context_aggregator.user()
    assistant_context_aggregator = context_aggregator.assistant()

    # Register user idle event handlers
    user_idle_handler = engine.create_user_idle_handler()

    @user_context_aggregator.event_handler("on_user_turn_idle")
    async def on_user_turn_idle(aggregator):
        await user_idle_handler.handle_idle(aggregator)

    @user_context_aggregator.event_handler("on_user_turn_started")
    async def on_user_turn_started(aggregator, strategy):
        user_idle_handler.reset()

    # Build pipeline:
    # transport.input → speech_injector → user_aggregator → LLM → TTS → transport.output → assistant_aggregator
    pipeline = Pipeline(
        [
            transport.input(),
            user_speech_injector,
            user_context_aggregator,
            mock_llm,
            tts,
            transport.output(),
            assistant_context_aggregator,
        ]
    )

    task = PipelineWorker(pipeline, params=PipelineParams(), enable_rtvi=False)
    engine.set_task(task)

    return engine, transport, task, user_idle_handler


class TestUserIdleHandler:
    """Test user idle handling with realistic conversation flows."""

    @pytest.mark.asyncio
    async def test_idle_does_not_trigger_during_active_conversation(
        self, three_node_workflow_no_variable_extraction: WorkflowGraph
    ):
        """Test that idle handler does not fire when users actively converse.

        Conversation flow:
        1. Bot: "Hello" (short greeting)
        2. User: "Hello" (injected after bot finishes speaking)
        3. Bot: longer response (TTS duration 400ms > idle timeout 200ms)
        4. User: "I need help with my account" (injected after bot finishes)
        5. Bot: collect_info function call (Start → Agent transition)
        6. Bot: end_call function call (Agent → End, ends conversation)

        Verifies:
        - User idle handler never triggers during active conversation
        - TTS duration exceeding idle timeout doesn't cause false idle triggers
        - Pipeline completes all 4 LLM steps
        """
        user_idle_timeout = 0.8

        mock_steps = [
            # Step 0: Short greeting on Start node
            MockLLMService.create_text_chunks("Hello"),
            # Step 1: Longer response (TTS 400ms > idle timeout 200ms)
            MockLLMService.create_text_chunks(
                "I can help you with your account. Let me look into that for you. "
                "Please hold on while I pull up your information."
            ),
            # Step 2: Transition from Start → Agent node
            MockLLMService.create_function_call_chunks(
                function_name="collect_info",
                arguments={},
                tool_call_id="call_collect_info",
            ),
            # Step 3: Transition from Agent → End node (ends call)
            MockLLMService.create_function_call_chunks(
                function_name="end_call",
                arguments={},
                tool_call_id="call_end_call",
            ),
        ]

        llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)

        (
            engine,
            transport,
            task,
            user_idle_handler,
        ) = await create_pipeline_with_speech_injection(
            workflow=three_node_workflow_no_variable_extraction,
            mock_llm=llm,
            speeches=["Hello", "I need help with my account"],
            user_idle_timeout=user_idle_timeout,
            mock_audio_duration_ms=400,
        )

        with patch(
            "api.db:db_client.get_organization_id_by_workflow_run_id",
            new_callable=AsyncMock,
            return_value=1,
        ):
            await run_engine_test_pipeline(task, engine, transport)

        # All 5 LLM steps should have been consumed
        assert llm.get_current_step() == 5

        # Idle handler should never have triggered
        assert user_idle_handler._retry_count == 0, (
            "User idle handler should not trigger during active conversation"
        )


IDLE_REASON = EndTaskReason.USER_IDLE_MAX_DURATION_EXCEEDED.value


@pytest.fixture
async def farewell_engine():
    engine = PipecatEngine(workflow=None, call_context_vars={}, workflow_run_id=1)
    engine.task = SimpleNamespace(queue_frame=AsyncMock())
    with patch.object(engine, "perform_final_variable_extraction", AsyncMock()):
        yield engine
        await engine.cleanup()


def assert_idle_ended(engine, disposition=IDLE_REASON):
    engine.perform_final_variable_extraction.assert_awaited_once()
    engine.task.queue_frame.assert_awaited_once()
    frame = engine.task.queue_frame.call_args.args[0]
    assert isinstance(frame, EndFrame)
    assert frame.reason == IDLE_REASON
    assert engine._gathered_context["call_status"] == IDLE_REASON
    assert engine._gathered_context["call_disposition"] == disposition
    assert engine._gathered_context[
        "mapped_call_disposition"
    ] == engine.map_disposition(disposition)
    assert engine._pending_farewell_task is None


@pytest.mark.asyncio
async def test_first_idle_and_reset_do_not_arm_termination(farewell_engine):
    engine = farewell_engine
    handler = engine.create_user_idle_handler()
    sink = SimpleNamespace(push_frame=AsyncMock())
    for _ in range(2):
        await handler.handle_idle(sink)
        frame = sink.push_frame.call_args.args[0]
        assert isinstance(frame, LLMMessagesAppendFrame)
        assert frame.run_llm is True
        assert "ask if they're still there" in frame.messages[0]["content"]
        assert engine._pending_farewell_task is None
        assert not engine._mute_pipeline
        handler.reset()
    engine.task.queue_frame.assert_not_awaited()


@pytest.mark.asyncio
async def test_supervisor_suppresses_idle_farewell(farewell_engine):
    engine = farewell_engine
    engine.answer_supervisor = SimpleNamespace(blocks_workflow=True)
    handler = engine.create_user_idle_handler()
    handler._retry_count = 1
    sink = SimpleNamespace(push_frame=AsyncMock())
    await handler.handle_idle(sink)
    assert handler._retry_count == 1
    assert engine._pending_farewell_task is None
    sink.push_frame.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("disposition", [None, "qualified"])
async def test_idle_waits_for_delayed_playback_and_rejects_duplicates(
    farewell_engine, disposition
):
    engine = farewell_engine
    if disposition:
        engine._disposition_mapping = {disposition: "Qualified lead"}
        engine.set_call_disposition(disposition)
    handler = engine.create_user_idle_handler()
    handler._retry_count = 1
    sink = SimpleNamespace(push_frame=AsyncMock())

    await handler.handle_idle(sink)
    waiter = engine._pending_farewell_task
    assert waiter is not None
    assert engine._mute_pipeline
    assert not engine.is_call_disposed()
    assert "call_status" not in engine._gathered_context
    frame = sink.push_frame.call_args.args[0]
    assert isinstance(frame, LLMMessagesAppendFrame)
    assert frame.run_llm is True
    assert "Wish them a good day" in frame.messages[0]["content"]
    engine.perform_final_variable_extraction.assert_not_awaited()
    engine.task.queue_frame.assert_not_awaited()

    assert await engine.should_mute_user(BotStartedSpeakingFrame())
    await handler.handle_idle(sink)
    await handler.handle_idle(sink)
    assert engine._pending_farewell_task is waiter
    assert engine._speech_playback_started.is_set(), "duplicates must not rearm"
    sink.push_frame.assert_awaited_once()
    await asyncio.sleep(0)
    engine.perform_final_variable_extraction.assert_not_awaited()
    engine.task.queue_frame.assert_not_awaited()

    assert await engine.should_mute_user(BotStoppedSpeakingFrame())
    await asyncio.wait_for(waiter, 1)
    assert_idle_ended(engine, disposition or IDLE_REASON)
    await handler.handle_idle(sink)
    sink.push_frame.assert_awaited_once()
    assert engine._speech_playback_finished.is_set(), "disposed calls must not rearm"


@pytest.mark.asyncio
async def test_idle_ignores_previous_utterance_stop(farewell_engine):
    engine = farewell_engine
    await engine.should_mute_user(BotStartedSpeakingFrame())
    assert engine.defer_end_call_until_bot_playback(IDLE_REASON)
    waiter = engine._pending_farewell_task
    await engine.should_mute_user(BotStoppedSpeakingFrame())
    await asyncio.sleep(0)
    engine.task.queue_frame.assert_not_awaited()
    assert not waiter.done()
    await engine.should_mute_user(BotStartedSpeakingFrame())
    assert not engine._speech_playback_finished.is_set()
    await asyncio.sleep(0)
    engine.task.queue_frame.assert_not_awaited()
    await engine.should_mute_user(BotStoppedSpeakingFrame())
    await asyncio.wait_for(waiter, 1)
    assert_idle_ended(engine)


@pytest.mark.asyncio
async def test_playback_inside_prompt_delivery_is_not_missed(farewell_engine):
    engine = farewell_engine
    handler = engine.create_user_idle_handler()
    handler._retry_count = 1
    waiters = []

    async def push_frame(frame):
        waiters.append(engine._pending_farewell_task)
        assert waiters[0] is not None
        await engine.should_mute_user(BotStartedSpeakingFrame())
        await engine.should_mute_user(BotStoppedSpeakingFrame())

    await handler.handle_idle(SimpleNamespace(push_frame=push_frame))
    await asyncio.wait_for(waiters[0], 1)
    assert_idle_ended(engine)


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt_result", ["no_audio", "start_only", "failure"])
async def test_idle_fallback_is_bounded_after_missing_audio(
    farewell_engine, prompt_result
):
    engine = farewell_engine
    handler = engine.create_user_idle_handler()
    handler._retry_count = 1

    async def push_frame(frame):
        if prompt_result == "start_only":
            await engine.should_mute_user(BotStartedSpeakingFrame())
        elif prompt_result == "failure":
            raise RuntimeError("provider unavailable")

    with patch.object(
        engine,
        "defer_end_call_until_bot_playback",
        side_effect=lambda reason: PipecatEngine.defer_end_call_until_bot_playback(
            engine, reason, fallback_secs=0.02
        ),
    ):
        if prompt_result == "failure":
            with pytest.raises(RuntimeError, match="provider unavailable"):
                await handler.handle_idle(SimpleNamespace(push_frame=push_frame))
        else:
            await handler.handle_idle(SimpleNamespace(push_frame=push_frame))
    waiter = engine._pending_farewell_task
    await asyncio.wait_for(waiter, 1)
    assert_idle_ended(engine)


@pytest.mark.asyncio
@pytest.mark.parametrize("played", [False, True])
async def test_playback_deadline_does_not_cancel_extraction_or_queueing(
    farewell_engine, played
):
    engine = farewell_engine
    extracting = asyncio.Event()
    finish_extraction = asyncio.Event()
    queueing = asyncio.Event()
    finish_queueing = asyncio.Event()

    async def extract():
        extracting.set()
        await finish_extraction.wait()

    async def queue(frame):
        queueing.set()
        await finish_queueing.wait()

    engine.perform_final_variable_extraction.side_effect = extract
    engine.task.queue_frame.side_effect = queue
    engine.defer_end_call_until_bot_playback(IDLE_REASON, fallback_secs=0.01)
    waiter = engine._pending_farewell_task
    if played:
        await engine.should_mute_user(BotStartedSpeakingFrame())
        await engine.should_mute_user(BotStoppedSpeakingFrame())
    await asyncio.wait_for(extracting.wait(), 1)
    assert not waiter.done()
    assert engine._pending_farewell_task is None
    # Hold both teardown stages beyond the playback deadline, including when
    # playback completed early. Neither stage belongs to that timeout scope.
    asyncio.get_running_loop().call_later(0.03, finish_extraction.set)
    await asyncio.wait_for(queueing.wait(), 1)
    assert not waiter.done()
    asyncio.get_running_loop().call_later(0.03, finish_queueing.set)
    await asyncio.wait_for(waiter, 1)
    assert_idle_ended(engine)


@pytest.mark.asyncio
async def test_stop_at_deadline_disposes_once(farewell_engine):
    engine = farewell_engine
    loop = asyncio.get_running_loop()
    stop_task = None

    def stop():
        nonlocal stop_task
        stop_task = asyncio.create_task(
            engine.should_mute_user(BotStoppedSpeakingFrame())
        )

    # Schedule the stop at the same deadline used by the waiter.
    timeout_at = asyncio.timeout_at

    def timeout_with_stop(deadline):
        loop.call_at(deadline, stop)
        return timeout_at(deadline)

    with patch(
        "api.services.workflow.pipecat_engine.asyncio.timeout_at",
        side_effect=timeout_with_stop,
    ):
        engine.defer_end_call_until_bot_playback(IDLE_REASON, fallback_secs=0.02)
        waiter = engine._pending_farewell_task
        await engine.should_mute_user(BotStartedSpeakingFrame())
        await asyncio.wait_for(waiter, 1)
    assert stop_task is not None
    await stop_task
    assert_idle_ended(engine)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason,abort",
    [
        (EndTaskReason.USER_HANGUP.value, False),
        (EndTaskReason.PIPELINE_ERROR.value, True),
        (EndTaskReason.CALL_DURATION_EXCEEDED.value, True),
    ],
)
async def test_other_termination_wins_during_farewell(farewell_engine, reason, abort):
    engine = farewell_engine
    engine.defer_end_call_until_bot_playback(IDLE_REASON, fallback_secs=60)
    waiter = engine._pending_farewell_task
    await asyncio.sleep(0)
    if reason == EndTaskReason.CALL_DURATION_EXCEEDED.value:
        await asyncio.wait_for(engine.create_max_duration_callback()(), 1)
    else:
        await asyncio.wait_for(engine.end_call_with_reason(reason, abort), 1)
    with pytest.raises(asyncio.CancelledError):
        await waiter
    await engine.should_mute_user(BotStartedSpeakingFrame())
    await engine.should_mute_user(BotStoppedSpeakingFrame())
    engine.task.queue_frame.assert_awaited_once()
    frame = engine.task.queue_frame.call_args.args[0]
    assert isinstance(frame, CancelFrame if abort else EndFrame)
    assert frame.reason == reason
    assert engine._gathered_context["call_status"] == reason
    assert engine._pending_farewell_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True])
async def test_cleanup_awaits_farewell_cancellation(farewell_engine, started):
    engine = farewell_engine
    engine.defer_end_call_until_bot_playback(IDLE_REASON)
    waiter = engine._pending_farewell_task
    if started:
        await asyncio.sleep(0)
    await engine.cleanup()
    assert waiter.cancelled()
    assert engine._pending_farewell_task is None
    assert not engine.is_call_disposed()
    engine.perform_final_variable_extraction.assert_not_awaited()
    engine.task.queue_frame.assert_not_awaited()


class DeferredRealtimeService(FrameProcessor):
    """A provider request completes before its audio arrives over the socket."""

    def __init__(self):
        super().__init__()
        self.request_returned = asyncio.Event()
        self.allow_audio = asyncio.Event()
        self.response_task = None
        self.audio = MockTTSService.create_mock_audio(200)

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMMessagesAppendFrame):
            assert frame.run_llm
            self.response_task = asyncio.create_task(self._respond())
            self.request_returned.set()
        else:
            await self.push_frame(frame, direction)

    async def _respond(self):
        await self.allow_audio.wait()
        await self.push_frame(TTSStartedFrame())
        await self.push_frame(TTSAudioRawFrame(self.audio, 16000, 1))
        await self.push_frame(TTSStoppedFrame())

    async def cleanup(self):
        if self.response_task:
            self.response_task.cancel()
            await asyncio.gather(self.response_task, return_exceptions=True)
        await super().cleanup()


@pytest.mark.asyncio
async def test_realtime_idle_audio_finishes_before_pipeline_teardown(farewell_engine):
    engine = farewell_engine
    service = DeferredRealtimeService()
    transport = MockTransport(
        params=TransportParams(
            audio_out_enabled=True,
            audio_out_sample_rate=16000,
            audio_out_end_silence_secs=0,
        )
    )
    events = []
    written = bytearray()
    write_audio = transport.output().write_audio_frame

    async def record_audio(frame):
        result = await write_audio(frame)
        written.extend(frame.audio)
        return result

    async def mute(frame):
        if isinstance(frame, BotStartedSpeakingFrame):
            events.append("start")
        elif isinstance(frame, BotStoppedSpeakingFrame):
            events.append("stop")
        return await engine.should_mute_user(frame)

    pair = LLMContextAggregatorPair(
        LLMContext(),
        user_params=LLMUserAggregatorParams(
            user_mute_strategies=[CallbackUserMuteStrategy(should_mute_callback=mute)],
            user_idle_timeout=0,
        ),
    )
    task = PipelineWorker(
        Pipeline(
            [
                transport.input(),
                pair.user(),
                service,
                transport.output(),
                pair.assistant(),
            ]
        ),
        params=PipelineParams(),
        enable_rtvi=False,
    )
    engine.set_task(task)
    handler = engine.create_user_idle_handler()
    handler._retry_count = 1

    @task.event_handler("on_pipeline_started")
    async def started(_task, _frame):
        await handler.handle_idle(pair.user())

    @task.event_handler("on_pipeline_finished")
    async def finished(_task, _frame):
        events.append("teardown")
        await engine.cleanup()

    async def extract():
        assert events == ["start", "stop"]
        assert bytes(written).startswith(service.audio)
        events.append("extraction")

    engine.perform_final_variable_extraction.side_effect = extract
    with patch.object(
        transport.output(), "write_audio_frame", side_effect=record_audio
    ):
        runner = asyncio.create_task(run_pipeline_worker(task))
        try:
            await asyncio.wait_for(service.request_returned.wait(), 2)
            assert events == []
            assert not engine.is_call_disposed()
            engine.perform_final_variable_extraction.assert_not_awaited()
            service.allow_audio.set()
            await asyncio.wait_for(runner, 5)
        finally:
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
    assert events == ["start", "stop", "extraction", "teardown"]
    assert engine._gathered_context["call_status"] == IDLE_REASON
    engine.perform_final_variable_extraction.assert_awaited_once()
    assert engine._pending_farewell_task is None


@pytest.mark.asyncio
async def test_cascaded_silent_caller_hears_complete_idle_farewell(
    three_node_workflow_no_variable_extraction,
):
    llm = MockLLMService(
        mock_steps=[
            MockLLMService.create_text_chunks("Hello."),
            MockLLMService.create_text_chunks("Are you still there?"),
            MockLLMService.create_text_chunks("Have a good day. Goodbye."),
        ],
        chunk_delay=0.001,
    )
    engine, transport, task, handler = await create_pipeline_with_speech_injection(
        three_node_workflow_no_variable_extraction,
        llm,
        speeches=[],
        user_idle_timeout=0.1,
        mock_audio_duration_ms=200,
    )
    extraction = AsyncMock()
    playback_at_end = []

    async def extract():
        playback_at_end.append(
            (
                engine._speech_playback_started.is_set(),
                engine._speech_playback_finished.is_set(),
            )
        )
        assert not engine._bot_is_speaking

    extraction.side_effect = extract
    with (
        patch(
            "api.db:db_client.get_organization_id_by_workflow_run_id",
            AsyncMock(return_value=1),
        ),
        patch.object(engine, "perform_final_variable_extraction", extraction),
    ):
        try:
            await run_engine_test_pipeline(task, engine, transport)
        finally:
            await engine.cleanup()
    assert llm.get_current_step() == 3
    assert handler._retry_count == 2
    extraction.assert_awaited_once()
    assert playback_at_end == [(True, True)]
    assert engine._gathered_context["call_status"] == IDLE_REASON
    assert engine._pending_farewell_task is None
