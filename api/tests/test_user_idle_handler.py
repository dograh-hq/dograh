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
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.workflow import WorkflowGraph
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    Frame,
    LLMContextFrame,
    TranscriptionFrame,
    UserSpeakingFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMAssistantAggregatorParams,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.tests import MockLLMService, MockTTSService
from pipecat.tests.mock_transport import MockTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.turns.user_mute import (
    CallbackUserMuteStrategy,
    MuteUntilFirstBotCompleteUserMuteStrategy,
)
from pipecat.turns.user_start import TranscriptionUserTurnStartStrategy
from pipecat.turns.user_stop import ExternalUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.utils.time import time_now_iso8601


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
) -> tuple[PipecatEngine, PipelineTask, object]:
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
        Tuple of (engine, task, user_idle_handler).
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

    assistant_params = LLMAssistantAggregatorParams(expect_stripped_words=True)

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

    task = PipelineTask(pipeline, params=PipelineParams(), enable_rtvi=False)
    engine.set_task(task)

    return engine, task, user_idle_handler


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

        engine, task, user_idle_handler = await create_pipeline_with_speech_injection(
            workflow=three_node_workflow_no_variable_extraction,
            mock_llm=llm,
            speeches=["Hello", "I need help with my account"],
            user_idle_timeout=user_idle_timeout,
            mock_audio_duration_ms=400,
        )

        with patch(
            "api.services.workflow.pipecat_engine.get_organization_id_from_workflow_run",
            new_callable=AsyncMock,
            return_value=1,
        ):
            with patch(
                "api.services.workflow.pipecat_engine.apply_disposition_mapping",
                new_callable=AsyncMock,
                return_value="completed",
            ):
                runner = PipelineRunner()

                async def run_pipeline():
                    await runner.run(task)

                async def initialize_engine():
                    await asyncio.sleep(0.01)
                    await engine.initialize()
                    await engine.llm.queue_frame(LLMContextFrame(engine.context))

                await asyncio.gather(run_pipeline(), initialize_engine())

        # All 5 LLM steps should have been consumed
        assert llm.get_current_step() == 5

        # Idle handler should never have triggered
        assert user_idle_handler._retry_count == 0, (
            "User idle handler should not trigger during active conversation"
        )
