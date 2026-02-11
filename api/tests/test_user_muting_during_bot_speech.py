"""Tests for verifying user muting behavior based on bot speaking state.

This module tests the user muting behavior with different allow_interrupt settings:

1. Pipeline is always muted until first BotStoppedSpeaking
2. When allow_interrupt=True, pipeline is NOT muted after second BotStartedSpeaking
3. When allow_interrupt=False, pipeline IS muted during second bot speech

The observer is placed BEFORE user_aggregator to check mute status when
bot speaking events flow upstream.
"""

import asyncio
from typing import List
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_variable_extractor import (
    VariableExtractionManager,
)
from api.services.workflow.workflow import WorkflowGraph
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    LLMContextFrame,
    TranscriptionFrame,
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
    LLMUserAggregator,
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
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies
from pipecat.utils.time import time_now_iso8601


class BotSpeakingObserverProcessor(FrameProcessor):
    """Observer that records mute status when bot speaking events flow upstream.

    Placed BEFORE user_aggregator in the pipeline. When bot speaking frames
    flow upstream (from output transport), they pass through user_aggregator
    first (updating its state), then reach this observer.

    Pipeline structure:
    transport.input() -> observer -> user_aggregator -> llm -> tts -> transport.output()

    UPSTREAM flow: transport.output() -> tts -> llm -> user_aggregator -> observer -> transport.input()
    """

    def __init__(self, user_aggregator: LLMUserAggregator, **kwargs):
        super().__init__(**kwargs)
        self.user_aggregator = user_aggregator
        self.bot_started_count = 0
        self.bot_stopped_count = 0
        self.mute_status_on_bot_started: List[bool] = []
        self.mute_status_on_bot_stopped: List[bool] = []

        # Events for synchronization
        self.first_bot_started = asyncio.Event()
        self.first_bot_stopped = asyncio.Event()
        self.second_bot_started = asyncio.Event()
        self.second_bot_stopped = asyncio.Event()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if direction == FrameDirection.UPSTREAM:
            if isinstance(frame, BotStartedSpeakingFrame):
                self.bot_started_count += 1
                # Check the current mute status from user_aggregator
                muted = self.user_aggregator._user_is_muted
                self.mute_status_on_bot_started.append(muted)

                if self.bot_started_count == 1:
                    self.first_bot_started.set()
                elif self.bot_started_count == 2:
                    self.second_bot_started.set()

            elif isinstance(frame, BotStoppedSpeakingFrame):
                self.bot_stopped_count += 1
                # Check the current mute status from user_aggregator
                muted = self.user_aggregator._user_is_muted
                self.mute_status_on_bot_stopped.append(muted)

                if self.bot_stopped_count == 1:
                    self.first_bot_stopped.set()
                elif self.bot_stopped_count == 2:
                    self.second_bot_stopped.set()

        await self.push_frame(frame, direction)


def set_workflow_allow_interrupt_in_start_node(
    workflow: WorkflowGraph, allow_interrupt: bool
):
    """Set allow_interrupt on all nodes in the workflow."""
    for node in workflow.nodes.values():
        if node.is_start:
            node.allow_interrupt = allow_interrupt


async def create_engine_for_mute_test(
    workflow: WorkflowGraph,
    mock_llm: MockLLMService,
    tts_duration_ms: int = 100,
) -> tuple[
    PipecatEngine,
    MockTTSService,
    MockTransport,
    PipelineTask,
    LLMUserAggregator,
    BotSpeakingObserverProcessor,
]:
    """Create a PipecatEngine with observer BEFORE user_aggregator for testing.

    Pipeline structure:
    transport.input() -> observer -> user_aggregator -> mock_llm -> tts -> transport.output() -> assistant_aggregator

    Returns:
        Tuple of (engine, tts, transport, task, user_aggregator, observer)
    """
    tts = MockTTSService(mock_audio_duration_ms=tts_duration_ms, frame_delay=0.01)

    mock_transport = MockTransport(
        generate_audio=False,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
        ),
    )

    context = LLMContext()

    engine = PipecatEngine(
        llm=mock_llm,
        context=context,
        workflow=workflow,
        call_context_vars={"customer_name": "Test User"},
        workflow_run_id=1,
    )

    # Create context aggregator with user mute strategies
    assistant_params = LLMAssistantAggregatorParams(expect_stripped_words=True)

    user_mute_strategies = [
        MuteUntilFirstBotCompleteUserMuteStrategy(),
        CallbackUserMuteStrategy(should_mute_callback=engine.should_mute_user),
    ]

    user_params = LLMUserAggregatorParams(
        user_turn_strategies=ExternalUserTurnStrategies(),
        user_mute_strategies=user_mute_strategies,
    )

    context_aggregator = LLMContextAggregatorPair(
        context, assistant_params=assistant_params, user_params=user_params
    )
    user_context_aggregator = context_aggregator.user()
    assistant_context_aggregator = context_aggregator.assistant()

    # Create observer with reference to user_aggregator
    observer = BotSpeakingObserverProcessor(user_context_aggregator)

    # Pipeline: observer is BEFORE user_aggregator
    # This means upstream frames (bot speaking) pass through user_aggregator first,
    # then reach the observer
    pipeline = Pipeline(
        [
            mock_transport.input(),
            observer,
            user_context_aggregator,
            mock_llm,
            tts,
            mock_transport.output(),
            assistant_context_aggregator,
        ]
    )

    task = PipelineTask(pipeline, params=PipelineParams(), enable_rtvi=False)
    engine.set_task(task)

    return engine, tts, mock_transport, task, user_context_aggregator, observer


async def queue_user_speaking_and_transcript_frames(task):
    await task.queue_frame(UserStartedSpeakingFrame())
    await asyncio.sleep(0)
    await task.queue_frame(
        TranscriptionFrame("User Speech", "user_id", time_now_iso8601())
    )
    await asyncio.sleep(0)
    await task.queue_frame(UserStoppedSpeakingFrame())


class TestUserMutingDuringBotSpeech:
    """Test user muting behavior based on bot speaking state."""

    @pytest.mark.asyncio
    async def test_muted_until_first_bot_stopped_speaking(
        self, simple_workflow: WorkflowGraph
    ):
        """Test that pipeline is always muted until first BotStoppedSpeaking.

        Both allow_interrupt=True and allow_interrupt=False should be muted
        during the first bot response due to MuteUntilFirstBotCompleteUserMuteStrategy.
        """
        set_workflow_allow_interrupt_in_start_node(
            simple_workflow, allow_interrupt=False
        )

        step_0_chunks = MockLLMService.create_text_chunks("Hello!")
        step_1_chunks = MockLLMService.create_text_chunks("How can I help?")
        mock_steps = [step_0_chunks, step_1_chunks]
        llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)

        (
            engine,
            _tts,
            _transport,
            task,
            _user_aggregator,
            observer,
        ) = await create_engine_for_mute_test(simple_workflow, llm, tts_duration_ms=50)

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
                with patch.object(
                    VariableExtractionManager,
                    "_perform_extraction",
                    new_callable=AsyncMock,
                    return_value={},
                ):
                    runner = PipelineRunner()

                    async def run_pipeline():
                        await runner.run(task)

                    async def run_test():
                        await asyncio.sleep(0.01)
                        await engine.initialize()

                        # Trigger first LLM completion
                        await engine.llm.queue_frame(LLMContextFrame(engine.context))

                        # Wait for first bot started
                        await asyncio.wait_for(
                            observer.first_bot_started.wait(), timeout=5.0
                        )

                        # Queue user speaking frames so that second generation starts
                        await queue_user_speaking_and_transcript_frames(task)

                        # Wait for first bot stopped
                        await asyncio.wait_for(
                            observer.first_bot_stopped.wait(), timeout=5.0
                        )

                        await task.cancel()

                    await asyncio.gather(
                        run_pipeline(),
                        run_test(),
                        return_exceptions=True,
                    )

        # VERIFY: Muted at first BotStartedSpeaking
        assert len(observer.mute_status_on_bot_started) >= 1
        assert observer.mute_status_on_bot_started[0] is True, (
            "Pipeline should be muted at first BotStartedSpeaking"
        )

        # VERIFY: Unmuted at first BotStoppedSpeaking
        assert len(observer.mute_status_on_bot_stopped) >= 1
        assert observer.mute_status_on_bot_stopped[0] is False, (
            "Pipeline should be unmuted at first BotStoppedSpeaking"
        )

    @pytest.mark.asyncio
    async def test_allow_interrupt_true_not_muted_after_second_bot_started(
        self, simple_workflow: WorkflowGraph
    ):
        """Test that when allow_interrupt=True, pipeline is NOT muted after second BotStartedSpeaking.

        After first bot response completes:
        - User speaks and triggers second LLM response
        - When second BotStartedSpeaking arrives, user should NOT be muted
          because allow_interrupt=True allows interruption
        """
        set_workflow_allow_interrupt_in_start_node(
            simple_workflow, allow_interrupt=True
        )

        step_0_chunks = MockLLMService.create_text_chunks("Hello!")
        step_1_chunks = MockLLMService.create_text_chunks("I can help with that.")
        mock_steps = [step_0_chunks, step_1_chunks]
        llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)

        (
            engine,
            _tts,
            _transport,
            task,
            _user_aggregator,
            observer,
        ) = await create_engine_for_mute_test(simple_workflow, llm, tts_duration_ms=50)

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
                with patch.object(
                    VariableExtractionManager,
                    "_perform_extraction",
                    new_callable=AsyncMock,
                    return_value={},
                ):
                    runner = PipelineRunner()

                    async def run_pipeline():
                        await runner.run(task)

                    async def run_test():
                        await asyncio.sleep(0.01)
                        await engine.initialize()

                        # Trigger first LLM completion
                        await engine.llm.queue_frame(LLMContextFrame(engine.context))

                        # Wait for first bot stopped (first response complete)
                        await asyncio.wait_for(
                            observer.first_bot_stopped.wait(), timeout=5.0
                        )

                        # Queue user speaking frames for second generation
                        await queue_user_speaking_and_transcript_frames(task)

                        # Wait for second bot started
                        await asyncio.wait_for(
                            observer.second_bot_started.wait(), timeout=5.0
                        )

                        # Wait for second bot stopped
                        await asyncio.wait_for(
                            observer.second_bot_stopped.wait(), timeout=5.0
                        )

                        await task.cancel()

                    await asyncio.gather(
                        run_pipeline(),
                        run_test(),
                        return_exceptions=True,
                    )

        # VERIFY: First bot started - should be muted (MuteUntilFirstBotComplete)
        assert len(observer.mute_status_on_bot_started) >= 2
        assert observer.mute_status_on_bot_started[0] is True, (
            "Pipeline should be muted at first BotStartedSpeaking"
        )

        # VERIFY: Second bot started - should NOT be muted (allow_interrupt=True)
        assert observer.mute_status_on_bot_started[1] is False, (
            "Pipeline should NOT be muted at second BotStartedSpeaking when allow_interrupt=True"
        )

    @pytest.mark.asyncio
    async def test_allow_interrupt_false_muted_during_second_bot_speech(
        self, simple_workflow: WorkflowGraph
    ):
        """Test that when allow_interrupt=False, pipeline IS muted during second bot speech.

        After first bot response completes:
        - User speaks and triggers second LLM response
        - When second BotStartedSpeaking arrives, user SHOULD be muted
          because allow_interrupt=False prevents interruption
        - When second BotStoppedSpeaking arrives, user should be unmuted
        """
        set_workflow_allow_interrupt_in_start_node(
            simple_workflow, allow_interrupt=False
        )

        step_0_chunks = MockLLMService.create_text_chunks("Hello!")
        step_1_chunks = MockLLMService.create_text_chunks("I can help with that.")
        mock_steps = [step_0_chunks, step_1_chunks]
        llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)

        (
            engine,
            _tts,
            _transport,
            task,
            _user_aggregator,
            observer,
        ) = await create_engine_for_mute_test(simple_workflow, llm, tts_duration_ms=50)

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
                with patch.object(
                    VariableExtractionManager,
                    "_perform_extraction",
                    new_callable=AsyncMock,
                    return_value={},
                ):
                    runner = PipelineRunner()

                    async def run_pipeline():
                        await runner.run(task)

                    async def run_test():
                        await asyncio.sleep(0.01)
                        await engine.initialize()

                        # Trigger first LLM completion
                        await engine.llm.queue_frame(LLMContextFrame(engine.context))

                        # Wait for first bot stopped (first response complete)
                        await asyncio.wait_for(
                            observer.first_bot_stopped.wait(), timeout=5.0
                        )

                        # Queue user speaking frames for second llm generation
                        await queue_user_speaking_and_transcript_frames(task)

                        # Wait for second bot started
                        await asyncio.wait_for(
                            observer.second_bot_started.wait(), timeout=5.0
                        )

                        # Wait for second bot stopped
                        await asyncio.wait_for(
                            observer.second_bot_stopped.wait(), timeout=5.0
                        )

                        await task.cancel()

                    await asyncio.gather(
                        run_pipeline(),
                        run_test(),
                        return_exceptions=True,
                    )

        # VERIFY: First bot started - should be muted (MuteUntilFirstBotComplete)
        assert len(observer.mute_status_on_bot_started) >= 2
        assert observer.mute_status_on_bot_started[0] is True, (
            "Pipeline should be muted at first BotStartedSpeaking"
        )

        # VERIFY: Second bot started - SHOULD be muted (allow_interrupt=False)
        assert observer.mute_status_on_bot_started[1] is True, (
            "Pipeline should be muted at second BotStartedSpeaking when allow_interrupt=False"
        )

        # VERIFY: Second bot stopped - should be unmuted
        assert len(observer.mute_status_on_bot_stopped) >= 2
        assert observer.mute_status_on_bot_stopped[1] is False, (
            "Pipeline should be unmuted at second BotStoppedSpeaking"
        )
