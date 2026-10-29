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
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    TranscriptionFrame,
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

from api.services.workflow import pipecat_engine_callbacks
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
        user_idle_timeout=0,
    )

    assistant_params = LLMAssistantAggregatorParams()

    context_aggregator = LLMContextAggregatorPair(
        context, assistant_params=assistant_params, user_params=user_params
    )
    user_context_aggregator = context_aggregator.user()
    assistant_context_aggregator = context_aggregator.assistant()

    user_idle_handler = engine.call_monitor
    user_idle_handler.bind_user(user_context_aggregator, idle_timeout=user_idle_timeout)

    # Build pipeline:
    # transport.input → speech_injector → user_aggregator → LLM → TTS → transport.output → assistant_aggregator
    pipeline = Pipeline(
        [
            transport.input(),
            user_speech_injector,
            user_context_aggregator,
            engine.call_monitor,
            mock_llm,
            tts,
            transport.output(),
            assistant_context_aggregator,
        ]
    )

    task = PipelineWorker(pipeline, params=PipelineParams(), enable_rtvi=False)
    engine.call_worker = task

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
async def farewell_engine(three_node_workflow_no_variable_extraction):
    workflow = three_node_workflow_no_variable_extraction
    engine = PipecatEngine(
        task=SimpleNamespace(queue_frame=AsyncMock()),
        workflow=workflow,
        call_context_vars={},
    )
    engine.active_agent.current_node = workflow.nodes["agent"]
    engine.perform_final_variable_extraction = AsyncMock()
    yield engine
    await engine.cleanup()


def play_response(engine):
    engine.speech_playback.after_output(None, LLMFullResponseStartFrame())
    engine.speech_playback.note_output()
    engine.speech_playback.after_output(None, LLMFullResponseEndFrame())


async def idle(engine, attempt, push_frame=None):
    sink = SimpleNamespace(push_frame=push_frame or AsyncMock())
    await pipecat_engine_callbacks.handle_user_idle(engine, sink, attempt)
    return sink


def assert_idle_ended(engine, disposition=IDLE_REASON):
    engine.perform_final_variable_extraction.assert_awaited_once()
    engine.call_worker.queue_frame.assert_awaited_once()
    frame = engine.call_worker.queue_frame.call_args.args[0]
    assert isinstance(frame, EndFrame)
    assert frame.reason == IDLE_REASON
    assert engine._gathered_context["call_status"] == IDLE_REASON
    assert engine._gathered_context["call_disposition"] == disposition
    assert engine._pending_farewell_task is None


@pytest.mark.asyncio
async def test_first_idle_does_not_arm_termination(farewell_engine):
    engine = farewell_engine
    sink = await idle(engine, 1)
    frame = sink.push_frame.call_args.args[0]
    assert isinstance(frame, LLMMessagesAppendFrame)
    assert frame.run_llm is True
    assert "ask if they're still there" in frame.messages[0]["content"]
    assert engine._pending_farewell_task is None
    assert not engine.speech_playback.pending


@pytest.mark.asyncio
@pytest.mark.parametrize("is_realtime", [True, False])
async def test_idle_waits_for_farewell_playback_and_rejects_duplicates(
    farewell_engine, is_realtime
):
    engine = farewell_engine
    engine._is_realtime = is_realtime
    # Playback that finished before the farewell must not count as it.
    await engine.should_mute_user(BotStartedSpeakingFrame())
    await engine.should_mute_user(BotStoppedSpeakingFrame())

    sink = await idle(engine, 2)
    waiter = engine._pending_farewell_task
    assert waiter is not None
    assert engine._mute_pipeline
    frame = sink.push_frame.call_args.args[0]
    assert isinstance(frame, LLMMessagesAppendFrame)
    assert frame.run_llm is True
    assert "Wish them a good day" in frame.messages[0]["content"]

    # The prompt returned before any audio: the call stays open.
    await asyncio.sleep(0)
    assert not engine.is_call_disposed()
    engine.perform_final_variable_extraction.assert_not_awaited()
    engine.call_worker.queue_frame.assert_not_awaited()

    await idle(engine, 3, sink.push_frame)
    assert engine._pending_farewell_task is waiter
    sink.push_frame.assert_awaited_once()

    engine.speech_playback.after_output(None, LLMFullResponseStartFrame())
    engine.speech_playback.note_output()
    await asyncio.sleep(0)
    engine.call_worker.queue_frame.assert_not_awaited()
    engine.speech_playback.after_output(None, LLMFullResponseEndFrame())

    await asyncio.wait_for(waiter, 1)
    assert_idle_ended(engine)
    await idle(engine, 4, sink.push_frame)
    sink.push_frame.assert_awaited_once()


@pytest.mark.asyncio
async def test_playback_inside_prompt_delivery_is_not_missed(farewell_engine):
    engine = farewell_engine
    waiters = []

    async def push_frame(frame):
        waiters.append(engine._pending_farewell_task)
        assert waiters[0] is not None
        play_response(engine)

    await idle(engine, 2, push_frame)
    await asyncio.wait_for(waiters[0], 1)
    assert_idle_ended(engine)


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt_result", ["no_audio", "start_only", "failure"])
async def test_idle_fallback_is_bounded_after_missing_audio(
    farewell_engine, prompt_result
):
    engine = farewell_engine

    async def push_frame(frame):
        if prompt_result == "start_only":
            engine.speech_playback.after_output(None, LLMFullResponseStartFrame())
            engine.speech_playback.note_output()
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
                await idle(engine, 2, push_frame)
        else:
            await idle(engine, 2, push_frame)
    await asyncio.wait_for(engine._pending_farewell_task, 1)
    assert_idle_ended(engine)


@pytest.mark.asyncio
async def test_farewell_ends_call_when_a_response_is_already_claimed(
    farewell_engine,
):
    engine = farewell_engine
    engine.speech_playback.expect_response(source=engine.active_agent.llm)
    assert engine.defer_end_call_until_bot_playback(IDLE_REASON)
    await asyncio.wait_for(engine._pending_farewell_task, 1)
    assert_idle_ended(engine)


@pytest.mark.asyncio
async def test_idle_farewell_queues_terminal_frame_if_end_call_raises(
    farewell_engine,
):
    engine = farewell_engine
    engine.perform_final_variable_extraction.side_effect = RuntimeError(
        "extract failed"
    )
    engine.defer_end_call_until_bot_playback(IDLE_REASON, fallback_secs=0.01)
    await asyncio.wait_for(engine._pending_farewell_task, 1)
    engine.call_worker.queue_frame.assert_awaited_once()
    frame = engine.call_worker.queue_frame.call_args.args[0]
    assert isinstance(frame, CancelFrame)
    assert frame.reason == EndTaskReason.PIPELINE_ERROR.value
    assert engine.is_call_disposed()
    assert engine._pending_farewell_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason,abort",
    [
        (EndTaskReason.USER_HANGUP.value, False),
        (EndTaskReason.PIPELINE_ERROR.value, True),
    ],
)
async def test_other_termination_wins_during_farewell(farewell_engine, reason, abort):
    engine = farewell_engine
    engine.defer_end_call_until_bot_playback(IDLE_REASON, fallback_secs=60)
    waiter = engine._pending_farewell_task
    await asyncio.sleep(0)
    await asyncio.wait_for(engine.end_call_with_reason(reason, abort), 1)
    with pytest.raises(asyncio.CancelledError):
        await waiter
    engine.call_worker.queue_frame.assert_awaited_once()
    frame = engine.call_worker.queue_frame.call_args.args[0]
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
    engine.call_worker.queue_frame.assert_not_awaited()
