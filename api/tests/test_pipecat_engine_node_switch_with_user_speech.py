"""Tests for verifying behavior when node switch and user speech happen simultaneously.

This module tests the interaction between node transitions and user speaking events
in the PipecatEngine. The key scenario being tested:

1. LLM calls a transition function to move from one node to another
2. At the same time, user starts and stops speaking (triggered by FunctionCallResultFrame)
3. The pipeline should handle both events correctly

The tests use a custom input transport that injects UserStartedSpeakingFrame and
UserStoppedSpeakingFrame when triggered by a FunctionCallResultFrame observer.
"""

import asyncio
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.workflow import WorkflowGraph
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    FunctionCallResultFrame,
    InputAudioRawFrame,
    LLMContextFrame,
    StartFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response import LLMAssistantAggregatorParams
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.tests import MockLLMService, MockTTSService
from pipecat.tests.mock_transport import MockOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams
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


class UserSpeechInjectingInputTransport(FrameProcessor):
    """Mock input transport that injects user speaking frames on FunctionCallResultFrame.

    This transport generates audio frames and automatically injects UserStartedSpeakingFrame
    and UserStoppedSpeakingFrame when it sees the first FunctionCallResultFrame flowing
    upstream through the pipeline.
    """

    def __init__(
        self,
        params: Optional[TransportParams] = None,
        *,
        generate_audio: bool = False,
        audio_interval_ms: int = 20,
        sample_rate: int = 16000,
        num_channels: int = 1,
        user_speech_initial_delay: float = 0.01,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._params = params or TransportParams()
        self._generate_audio = generate_audio
        self._audio_interval_ms = audio_interval_ms
        self._sample_rate = sample_rate
        self._num_channels = num_channels
        self._user_speech_initial_delay = user_speech_initial_delay
        self._audio_task: Optional[asyncio.Task] = None
        self._running = False
        self._function_call_result_count = 0

    async def _generate_audio_frames(self):
        """Generate audio frames at regular intervals."""
        samples_per_frame = int(self._sample_rate * self._audio_interval_ms / 1000)
        bytes_per_frame = samples_per_frame * self._num_channels * 2
        silence_audio = bytes(bytes_per_frame)

        while self._running:
            try:
                frame = InputAudioRawFrame(
                    audio=silence_audio,
                    sample_rate=self._sample_rate,
                    num_channels=self._num_channels,
                )
                await self.push_frame(frame)
                await asyncio.sleep(self._audio_interval_ms / 1000)
            except asyncio.CancelledError:
                break

    def _start_tasks(self):
        """Start audio generation task."""
        if not self._running:
            self._running = True
            if self._generate_audio:
                self._audio_task = asyncio.create_task(self._generate_audio_frames())

    def _stop_tasks(self):
        """Stop all background tasks."""
        self._running = False
        if self._audio_task and not self._audio_task.done():
            self._audio_task.cancel()
            self._audio_task = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._start_tasks()
        elif isinstance(frame, (EndFrame, CancelFrame)):
            self._stop_tasks()
        elif isinstance(frame, FunctionCallResultFrame):
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

    async def cleanup(self):
        self._stop_tasks()
        await super().cleanup()


class UserSpeechInjectingTransport(BaseTransport):
    """Transport that injects user speaking frames on first FunctionCallResultFrame."""

    def __init__(
        self,
        params: Optional[TransportParams] = None,
        *,
        input_name: Optional[str] = None,
        output_name: Optional[str] = None,
        emit_bot_speaking: bool = True,
        generate_audio: bool = False,
        audio_interval_ms: int = 20,
        audio_sample_rate: int = 16000,
        audio_num_channels: int = 1,
        user_speech_initial_delay: float = 0.01,
    ):
        super().__init__(input_name=input_name, output_name=output_name)
        self._params = params or TransportParams()
        self._input = UserSpeechInjectingInputTransport(
            self._params,
            name=self._input_name,
            generate_audio=generate_audio,
            audio_interval_ms=audio_interval_ms,
            sample_rate=audio_sample_rate,
            num_channels=audio_num_channels,
            user_speech_initial_delay=user_speech_initial_delay,
        )
        self._output = MockOutputTransport(
            self._params,
            emit_bot_speaking=emit_bot_speaking,
            name=self._output_name,
        )

    def input(self) -> UserSpeechInjectingInputTransport:
        return self._input

    def output(self) -> FrameProcessor:
        return self._output


async def create_test_pipeline(
    workflow: WorkflowGraph,
    mock_llm: MockLLMService,
    generate_audio: bool = True,
    user_speech_initial_delay: float = 0.01,
) -> tuple[PipecatEngine, UserSpeechInjectingTransport, PipelineTask]:
    """Create a PipecatEngine with full pipeline for testing node switch scenarios.

    The transport's input automatically injects UserStartedSpeakingFrame and
    UserStoppedSpeakingFrame when it sees the first FunctionCallResultFrame
    flowing upstream through the pipeline.

    Args:
        workflow: The workflow graph to use.
        mock_llm: The mock LLM service.
        generate_audio: If True, the mock transport generates InputAudioRawFrame
            every 20ms to simulate real audio input.
        user_speech_initial_delay: Delay in seconds before injecting
            UserStartedSpeakingFrame after seeing FunctionCallResultFrame.

    Returns:
        Tuple of (engine, transport, task)
    """
    # Create MockTTSService
    tts = MockTTSService(mock_audio_duration_ms=10, frame_delay=0)

    # Create custom transport that injects user speaking frames on FunctionCallResultFrame #1
    transport = UserSpeechInjectingTransport(
        generate_audio=generate_audio,
        audio_interval_ms=20,
        audio_sample_rate=16000,
        audio_num_channels=1,
        emit_bot_speaking=True,
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
    # transport.input() -> user_aggregator -> LLM -> TTS -> transport.output() -> assistant_aggregator
    # The transport input watches for FunctionCallResultFrame flowing upstream
    # and injects user speaking frames when it sees the first one
    pipeline = Pipeline(
        [
            transport.input(),
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
           UserStoppedSpeakingFrame are automatically injected from the pipeline source
        3. The pipeline processes both events concurrently

        The FunctionCallResultObserver in the pipeline detects the first function call
        result and triggers the transport to inject user speaking frames.

        This test is parameterized with two scenarios:
        - delayed_user_speech: 10ms delay before UserStartedSpeakingFrame (user_speech_initial_delay=0.01)
        - immediate_user_speech: No delay before UserStartedSpeakingFrame (user_speech_initial_delay=0)

        This is a scenario creation test - no specific assertions yet.
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
