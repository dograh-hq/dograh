"""Tests for understanding voicemail detector behavior with user aggregator and LLM.

This module tests the interaction between the voicemail detector, user aggregator,
and LLM in a pipeline. It demonstrates how the voicemail detector classifies
incoming speech as CONVERSATION or VOICEMAIL and how the main LLM responds.
"""

import asyncio

import pytest
from pipecat.extensions.voicemail.voicemail_detector import VoicemailDetector
from pipecat.frames.frames import (
    EndWorkerFrame,
    Frame,
    FunctionCallFromLLM,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    TranscriptionFrame,
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
from pipecat.services.llm_service import FunctionCallParams
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.turns.user_start import (
    TranscriptionUserTurnStartStrategy,
    VADUserTurnStartStrategy,
)
from pipecat.turns.user_stop import (
    ExternalUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.utils.time import time_now_iso8601

from api.services.pipecat.worker_runner import run_pipeline_worker
from pipecat.tests import MockLLMService


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
        5. Main LLM runs once more for the second turn

        Pipeline structure mirrors run_pipeline.py:
        injector -> voicemail_detector.detector() -> user_aggregator
                 -> voicemail_detector.llm_gate() -> main_llm -> assistant_aggregator
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

        assistant_params = LLMAssistantAggregatorParams()

        context_aggregator = LLMContextAggregatorPair(
            context, assistant_params=assistant_params, user_params=user_params
        )
        user_context_aggregator = context_aggregator.user()
        assistant_context_aggregator = context_aggregator.assistant()

        # The first generation responds after CONVERSATION classification.
        # The mock's second generation is intentionally empty.
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
                voicemail_detector.llm_gate(),
                main_llm,
                assistant_context_aggregator,
            ]
        )

        task = PipelineWorker(pipeline, params=PipelineParams(), enable_rtvi=False)

        async def run_pipeline():
            await run_pipeline_worker(task)

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
                EndWorkerFrame(), direction=FrameDirection.UPSTREAM
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

    @pytest.mark.asyncio
    async def test_function_result_after_conversation_does_not_retrigger_classifier(
        self,
    ):
        """A main-LLM tool result must not invoke the voicemail classifier again."""
        context = LLMContext()
        context_aggregator = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                user_turn_strategies=UserTurnStrategies(
                    start=[TranscriptionUserTurnStartStrategy()],
                    stop=[ExternalUserTurnStopStrategy()],
                )
            ),
        )

        main_llm = MockLLMService(
            mock_steps=[
                MockLLMService.create_function_call_chunks(
                    "lookup",
                    {"query": "test"},
                ),
                MockLLMService.create_text_chunks("Tool result handled."),
            ],
            chunk_delay=0.001,
        )

        async def lookup(params: FunctionCallParams):
            await params.result_callback({"status": "ok"})

        main_llm.register_function("lookup", lookup)

        voicemail_llm = MockLLMService(
            mock_steps=[
                MockLLMService.create_text_chunks("CONVERSATION"),
                MockLLMService.create_text_chunks("VOICEMAIL"),
            ],
            chunk_delay=0.001,
        )
        voicemail_detector = VoicemailDetector(llm=voicemail_llm)
        voicemail_events = []

        @voicemail_detector.event_handler("on_voicemail_detected")
        async def on_voicemail_detected(_processor):
            voicemail_events.append(True)

        injector = FrameInjector()
        pipeline = Pipeline(
            [
                injector,
                voicemail_detector.detector(),
                context_aggregator.user(),
                voicemail_detector.llm_gate(),
                main_llm,
                context_aggregator.assistant(),
            ]
        )
        task = PipelineWorker(pipeline, params=PipelineParams(), enable_rtvi=False)

        async def inject_frames():
            await asyncio.sleep(0.05)
            await injector.inject_frame(UserStartedSpeakingFrame())
            await asyncio.sleep(0.01)
            await injector.inject_frame(
                TranscriptionFrame("Hello", "user-123", time_now_iso8601())
            )
            await asyncio.sleep(0.05)
            await injector.inject_frame(UserStoppedSpeakingFrame())

            async with asyncio.timeout(1.0):
                while main_llm.get_current_step() < 2:
                    await asyncio.sleep(0.01)

            await injector.inject_frame(
                EndWorkerFrame(), direction=FrameDirection.UPSTREAM
            )

        await asyncio.gather(run_pipeline_worker(task), inject_frames())

        assert voicemail_llm.get_current_step() == 1
        assert main_llm.get_current_step() == 2
        assert voicemail_events == []

    @pytest.mark.asyncio
    async def test_function_result_before_speech_does_not_trigger_classifier(self):
        """Tool frames alone must not invoke voicemail classification."""
        voicemail_llm = MockLLMService(
            mock_steps=[MockLLMService.create_text_chunks("VOICEMAIL")],
            chunk_delay=0.001,
        )
        voicemail_detector = VoicemailDetector(llm=voicemail_llm)
        function_call = FunctionCallFromLLM(
            function_name="lookup",
            tool_call_id="call-1",
            arguments={"query": "test"},
            context=None,
        )

        await run_test(
            voicemail_detector.detector(),
            frames_to_send=[
                FunctionCallsStartedFrame(function_calls=[function_call]),
                FunctionCallInProgressFrame(
                    function_name="lookup",
                    tool_call_id="call-1",
                    arguments={"query": "test"},
                    cancel_on_interruption=True,
                ),
                FunctionCallResultFrame(
                    function_name="lookup",
                    tool_call_id="call-1",
                    arguments={"query": "test"},
                    result={"status": "ok"},
                ),
                SleepFrame(sleep=0.1),
            ],
            frames_to_send_direction=FrameDirection.UPSTREAM,
        )

        assert voicemail_llm.get_current_step() == 0
        assert voicemail_detector._context.messages == []
