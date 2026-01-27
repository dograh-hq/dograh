"""Tests for understanding voicemail detector behavior with user aggregator and LLM.

This module tests the interaction between the voicemail detector, user aggregator,
and LLM in a pipeline. It demonstrates how the voicemail detector classifies
incoming speech as CONVERSATION or VOICEMAIL and how the main LLM responds.
"""

import asyncio

import pytest

from pipecat.extensions.voicemail.voicemail_detector import VoicemailDetector
from pipecat.frames.frames import (
    EndTaskFrame,
    Frame,
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
from pipecat.tests import MockLLMService
from pipecat.turns.user_start import (
    TranscriptionUserTurnStartStrategy,
    VADUserTurnStartStrategy,
)
from pipecat.turns.user_stop import (
    ExternalUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.utils.time import time_now_iso8601


class FrameInjector(FrameProcessor):
    """Simple processor that can inject frames into the pipeline."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._frames_to_inject: list[Frame] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

    async def inject_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ):
        """Inject a frame into the pipeline."""
        await self.push_frame(frame, direction)


class FrameCounter:
    """Helper to count specific frame types seen by a processor."""

    def __init__(self):
        self.user_stopped_speaking_count = 0
        self.user_started_speaking_count = 0

    def wrap_process_frame(self, original_process_frame):
        """Wrap a process_frame method to count UserStoppedSpeakingFrame."""

        async def wrapped(frame: Frame, direction: FrameDirection):
            if isinstance(frame, UserStoppedSpeakingFrame):
                self.user_stopped_speaking_count += 1
            elif isinstance(frame, UserStartedSpeakingFrame):
                self.user_started_speaking_count += 1
            return await original_process_frame(frame, direction)

        return wrapped


class TestVoicemailDetectorWithUserAggregator:
    """Test scenarios with voicemail detector and user aggregator."""

    @pytest.mark.asyncio
    async def test_voicemail_detector_conversation_flow(self):
        """Test: Voicemail detector classifies as CONVERSATION and main LLM responds.

        This test bench shows the flow:
        1. User starts speaking, sends transcription, stops speaking
        2. Voicemail detector's internal LLM classifies as "CONVERSATION"
        3. Main LLM generates response text
        4. Second user turn with transcription
        5. Main LLM generates end_call function to end pipeline

        Pipeline structure mirrors run_pipeline.py:
        injector -> voicemail_detector.detector() -> user_aggregator -> main_llm
                 -> voicemail_detector.gate() -> assistant_aggregator
        """
        context = LLMContext()

        # Create user turn strategies
        user_turn_strategies = UserTurnStrategies(
            start=[
                VADUserTurnStartStrategy(),
                TranscriptionUserTurnStartStrategy(),
            ],
            stop=[ExternalUserTurnStopStrategy()],
        )

        user_params = LLMUserAggregatorParams(
            user_turn_strategies=user_turn_strategies,
        )

        assistant_params = LLMAssistantAggregatorParams(expect_stripped_words=True)

        context_aggregator = LLMContextAggregatorPair(
            context, assistant_params=assistant_params, user_params=user_params
        )
        user_context_aggregator = context_aggregator.user()
        assistant_context_aggregator = context_aggregator.assistant()

        # Create mock LLM for main conversation
        # Step 0: First response after CONVERSATION classification
        # Step 1: Response to second user turn
        # Step 2: end_call function call to end pipeline
        main_llm_steps = [
            MockLLMService.create_text_chunks(text="Hello! I'm here to help you today.")
        ]
        main_llm = MockLLMService(mock_steps=main_llm_steps, chunk_delay=0.001)

        # Create mock LLM for voicemail classification
        # First response: "CONVERSATION" to close the voicemail gate
        voicemail_classification_steps = [
            MockLLMService.create_text_chunks(text="CONVERSATION"),
        ]
        voicemail_llm = MockLLMService(
            mock_steps=voicemail_classification_steps, chunk_delay=0.001
        )

        # Create voicemail detector with the classification LLM
        voicemail_detector = VoicemailDetector(
            llm=voicemail_llm,
            voicemail_response_delay=0,
        )

        # Set up frame counter to track UserStoppedSpeakingFrame in voicemail detector's user aggregator
        voicemail_user_aggregator = voicemail_detector._context_aggregator.user()
        frame_counter = FrameCounter()
        original_process_frame = voicemail_user_aggregator.process_frame
        voicemail_user_aggregator.process_frame = frame_counter.wrap_process_frame(
            original_process_frame
        )

        # Build pipeline similar to run_pipeline.py structure
        injector = FrameInjector()
        pipeline = Pipeline(
            [
                injector,
                voicemail_detector.detector(),  # Classification parallel pipeline
                user_context_aggregator,
                main_llm,
                assistant_context_aggregator,
            ]
        )

        task = PipelineTask(pipeline, params=PipelineParams(), enable_rtvi=False)
        runner = PipelineRunner()

        async def run_pipeline():
            await runner.run(task)

        async def inject_frames():
            await asyncio.sleep(0.05)

            # === First user turn ===
            # This triggers voicemail classification AND main LLM response
            await injector.inject_frame(UserStartedSpeakingFrame())
            await asyncio.sleep(0)
            await injector.inject_frame(
                TranscriptionFrame("First User Speech", "user-123", time_now_iso8601())
            )
            await asyncio.sleep(0.05)
            await injector.inject_frame(UserStoppedSpeakingFrame())

            # Wait for voicemail classification and main LLM response
            await asyncio.sleep(0.2)

            # === Second user turn ===
            await injector.inject_frame(UserStartedSpeakingFrame())

            await asyncio.sleep(0)
            await injector.inject_frame(
                TranscriptionFrame(
                    "Second User Speech",
                    "user-123",
                    time_now_iso8601(),
                )
            )

            await asyncio.sleep(0.05)
            await injector.inject_frame(UserStoppedSpeakingFrame())

            await asyncio.sleep(0.05)
            await injector.inject_frame(
                EndTaskFrame(), direction=FrameDirection.UPSTREAM
            )

        await asyncio.gather(run_pipeline(), inject_frames())

        # Assert voicemail LLM was called once for classification
        assert voicemail_llm.get_current_step() == 1

        # Assert main LLM was called twice (once per user turn)
        assert main_llm.get_current_step() == 2

        # Assert voicemail detector's user aggregator saw UserStoppedSpeakingFrame only once
        # (because the classifier gate closes after CONVERSATION classification,
        # blocking subsequent frames from reaching the voicemail branch)
        assert frame_counter.user_stopped_speaking_count == 1, (
            f"Expected voicemail detector's user aggregator to see UserStoppedSpeakingFrame once, "
            f"but saw it {frame_counter.user_stopped_speaking_count} times"
        )

        # We should see no more than 2 user started speaking frame. One from downstream FrameInjector
        # and one from upstream main pipeline's LLMUserAggregator
        assert frame_counter.user_started_speaking_count <= 2, (
            f"Expected voicemail detector's user aggregator to see UserStartedSpeakingFrame at most twice, "
            f"but saw it {frame_counter.user_started_speaking_count} times"
        )

        # Assert the classifier gate is closed after classification
        assert voicemail_detector._classifier_gate._gate_opened is False, (
            "Expected classifier gate to be closed after CONVERSATION classification"
        )

        # Assert the classifier gate is closed after classification
        assert voicemail_detector._classifier_upstream_gate._gate_open is False, (
            "Expected classifier upstream gate to be closed after CONVERSATION classification"
        )
