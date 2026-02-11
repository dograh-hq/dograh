"""Tests for verifying behavior when node switch and user speech happen simultaneously.

This module tests the interaction between node transitions and user speaking events
in the PipecatEngine. The key scenario being tested:

1. LLM calls a transition function to move from one node to another
2. At the same time, user starts and stops speaking (triggered by FunctionCallResultFrame)
3. The pipeline should handle both events correctly

The tests use a UserSpeechInjector processor that injects UserStartedSpeakingFrame and
UserStoppedSpeakingFrame when triggered by a FunctionCallResultFrame.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.workflow import WorkflowGraph
from pipecat.frames.frames import (
    Frame,
    FunctionCallResultFrame,
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
from pipecat.turns.user_start import (
    TranscriptionUserTurnStartStrategy,
)
from pipecat.turns.user_stop import (
    ExternalUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.utils.time import time_now_iso8601


class UserSpeechInjector(FrameProcessor):
    """Processor that injects user speaking frames on FunctionCallResultFrame.

    When this processor sees the first FunctionCallResultFrame flowing upstream,
    it injects UserStartedSpeakingFrame, TranscriptionFrame, and UserStoppedSpeakingFrame
    downstream to simulate user speech during a function call.
    """

    def __init__(
        self,
        *,
        user_speech_initial_delay: float = 0.01,
        **kwargs,
    ):
        """Initialize the user speech injector.

        Args:
            user_speech_initial_delay: Delay in seconds before injecting
                UserStartedSpeakingFrame after seeing FunctionCallResultFrame.
            **kwargs: Additional arguments passed to parent class.
        """
        super().__init__(**kwargs)
        self._user_speech_initial_delay = user_speech_initial_delay
        self._function_call_result_count = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, FunctionCallResultFrame):
            # When we see FunctionCallResultFrame #1 flowing upstream,
            # inject user speaking frames downstream
            self._function_call_result_count += 1
            if self._function_call_result_count == 1:
                # Simulate first race condition to generate
                # LLM call close enough to the LLM call from
                # function call
                await asyncio.sleep(self._user_speech_initial_delay)
                await self.push_frame(UserStartedSpeakingFrame())

                await asyncio.sleep(0)
                await self.push_frame(
                    TranscriptionFrame("First User Speech", "abc", time_now_iso8601())
                )

                await asyncio.sleep(0)
                await self.push_frame(UserStoppedSpeakingFrame())

                # Generate second llm call
                await asyncio.sleep(0.1)
                await self.push_frame(UserStartedSpeakingFrame())

                await asyncio.sleep(0)
                await self.push_frame(
                    TranscriptionFrame("Second User Speech", "abc", time_now_iso8601())
                )

                await asyncio.sleep(0)
                await self.push_frame(UserStoppedSpeakingFrame())

        await self.push_frame(frame, direction)


async def create_test_pipeline(
    workflow: WorkflowGraph,
    mock_llm: MockLLMService,
    user_speech_initial_delay: float = 0.01,
) -> tuple[PipecatEngine, MockTransport, PipelineTask]:
    """Create a PipecatEngine with full pipeline for testing node switch scenarios.

    The pipeline includes a UserSpeechInjector processor that injects
    UserStartedSpeakingFrame and UserStoppedSpeakingFrame when it sees
    the first FunctionCallResultFrame flowing upstream.

    Args:
        workflow: The workflow graph to use.
        mock_llm: The mock LLM service.
        user_speech_initial_delay: Delay in seconds before injecting
            UserStartedSpeakingFrame after seeing FunctionCallResultFrame.

    Returns:
        Tuple of (engine, transport, task)
    """
    # Create MockTTSService
    tts = MockTTSService(mock_audio_duration_ms=40, frame_delay=0)

    # Create MockTransport
    transport = MockTransport(
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
        ),
    )

    # Create user speech injector processor
    user_speech_injector = UserSpeechInjector(
        user_speech_initial_delay=user_speech_initial_delay,
    )

    # Create LLM context
    context = LLMContext()

    # Create PipecatEngine with the workflow
    engine = PipecatEngine(
        llm=mock_llm,
        context=context,
        workflow=workflow,
        call_context_vars={"customer_name": "Test User"},
        workflow_run_id=1,
    )

    # Create user turn strategies matching run_pipeline.py
    user_turn_strategies = UserTurnStrategies(
        start=[TranscriptionUserTurnStartStrategy()],
        stop=[ExternalUserTurnStopStrategy()],
    )

    # Create user mute strategies matching run_pipeline.py
    user_mute_strategies = [
        MuteUntilFirstBotCompleteUserMuteStrategy(),
        CallbackUserMuteStrategy(should_mute_callback=engine.should_mute_user),
    ]

    user_params = LLMUserAggregatorParams(
        user_turn_strategies=user_turn_strategies,
        user_mute_strategies=user_mute_strategies,
    )

    # Create context aggregator with user and assistant params
    assistant_params = LLMAssistantAggregatorParams(expect_stripped_words=True)

    context_aggregator = LLMContextAggregatorPair(
        context, assistant_params=assistant_params, user_params=user_params
    )
    user_context_aggregator = context_aggregator.user()
    assistant_context_aggregator = context_aggregator.assistant()

    # Create the pipeline:
    # transport.input() -> user_speech_injector -> user_aggregator -> LLM -> TTS -> transport.output() -> assistant_aggregator
    # The user_speech_injector watches for FunctionCallResultFrame flowing upstream
    # and injects user speaking frames when it sees the first one
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

    # Create pipeline task
    task = PipelineTask(pipeline, params=PipelineParams(), enable_rtvi=False)

    engine.set_task(task)

    return engine, transport, task


class TestNodeSwitchWithUserSpeech:
    """Test scenarios where node switch and user speech happen simultaneously."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "user_speech_initial_delay,scenario_name",
        [
            (0.01, "delayed"),
            (0, "immediate"),
        ],
        ids=["delayed_user_speech", "immediate_user_speech"],
    )
    async def test_node_switch_with_concurrent_user_speech(
        self,
        three_node_workflow_no_variable_extraction: WorkflowGraph,
        user_speech_initial_delay: float,
        scenario_name: str,
    ):
        """Test scenario: node transition happens while user is speaking.

        This test creates the scenario where:
        1. LLM generates text and calls collect_info to transition from start to agent
        2. When FunctionCallResultFrame #1 is seen, UserStartedSpeakingFrame and
           UserStoppedSpeakingFrame are automatically injected by UserSpeechInjector
        3. The pipeline processes both events concurrently

        The UserSpeechInjector processor in the pipeline detects the first function call
        result and injects user speaking frames.

        This test is parameterized with two scenarios:
        - delayed_user_speech: 10ms delay before UserStartedSpeakingFrame (user_speech_initial_delay=0.01)
        - immediate_user_speech: No delay before UserStartedSpeakingFrame (user_speech_initial_delay=0)
        """
        # Step 0 (Start node): greet user then call collect_info to transition to agent
        step_0_chunks = MockLLMService.create_mixed_chunks(
            text="Hello!",
            function_name="collect_info",
            arguments={},
            tool_call_id="call_transition_1",
        )

        step_1_chunks = MockLLMService.create_text_chunks(
            text="Step 1 with some longer text that should cause multiple chunks to be created."
        )

        step_2_chunks = MockLLMService.create_function_call_chunks(
            function_name="end_call",
            arguments={},
            tool_call_id="call_transition_2",
        )

        mock_steps = [step_0_chunks, step_1_chunks, step_2_chunks]
        llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)

        engine, _transport, task = await create_test_pipeline(
            three_node_workflow_no_variable_extraction,
            llm,
            user_speech_initial_delay=user_speech_initial_delay,
        )

        # Patch DB calls
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
                    # Start the LLM generation - user speech will be injected
                    # automatically when FunctionCallResultFrame #1 is seen
                    await engine.llm.queue_frame(LLMContextFrame(engine.context))

                await asyncio.gather(run_pipeline(), initialize_engine())

        # Total 4 generations out of which 1 was cancelled due to interruption
        assert llm.get_current_step() == 4
