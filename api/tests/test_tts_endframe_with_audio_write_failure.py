"""Tests for TTS pause_frame_processing with audio write failure scenarios.

This module tests a scenario where:
1. TTS service has pause_frame_processing=True
2. Output transport's write_audio_frame returns False (simulating failure)
3. TTS pauses frame processing while generating audio
4. Audio write failures occur but BotStoppedSpeakingFrame is never sent
5. TTS remains paused indefinitely
6. end_call_with_reason is called and hangs because EndFrame can't be processed

The root cause is that when write_audio_frame fails consecutively in _audio_task_handler,
it breaks out of the loop without calling _bot_stopped_speaking(), leaving the TTS
in a paused state that blocks all subsequent frame processing including EndFrame.

Two test scenarios are covered:
1. Bot started speaking, then audio write fails (fail_after_n_frames > 0)
   - BotStartedSpeakingFrame is emitted
   - Some audio is written successfully
   - Write starts failing, _audio_task_handler breaks out
   - _bot_stopped_speaking() is NOT called (the bug)
   - TTS remains paused

2. Bot never started speaking because write failed immediately (fail_after_n_frames = 0)
   - Audio write fails from the first frame
   - _bot_currently_speaking() is called but write fails
   - _audio_task_handler breaks out after consecutive failures
   - _bot_stopped_speaking() is NOT called (the bug)
   - TTS remains paused
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_variable_extractor import (
    VariableExtractionManager,
)
from api.services.workflow.workflow import WorkflowGraph
from pipecat.frames.frames import LLMContextFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response import LLMAssistantAggregatorParams
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.tests import MockLLMService, MockTTSService
from pipecat.tests.mock_transport import MockTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.turns.user_mute import (
    CallbackUserMuteStrategy,
    MuteUntilFirstBotCompleteUserMuteStrategy,
)
from pipecat.utils.enums import EndTaskReason


async def create_test_pipeline_with_failing_transport(
    workflow: WorkflowGraph,
    mock_llm: MockLLMService,
    fail_after_n_frames: int = 0,
) -> tuple[PipecatEngine, MockTTSService, MockTransport, PipelineTask]:
    """Create a PipecatEngine with failing output transport for testing.

    Uses the real MockTransport which now extends BaseOutputTransport and uses
    the real MediaSender machinery. This properly simulates:
    - Bot speaking events through _handle_bot_speech and _bot_currently_speaking
    - Audio write failure handling in _audio_task_handler
    - The bug where _bot_stopped_speaking() is not called after consecutive failures

    Args:
        workflow: The workflow graph to use.
        mock_llm: The mock LLM service.
        fail_after_n_frames: Number of audio frames that will succeed before
            write starts failing. Set to 0 to fail immediately.

    Returns:
        Tuple of (engine, tts, transport, task)
    """
    # Create TTS with pause_frame_processing=True
    # This causes TTS to pause processing frames while generating audio,
    # waiting for BotStoppedSpeakingFrame to resume
    tts = MockTTSService(
        mock_audio_duration_ms=200,  # Shorter for faster test
        frame_delay=0.001,  # Minimal delay
        pause_frame_processing=True,  # Key setting for this test
    )

    # Create transport that fails audio writes
    # Uses the real MediaSender._audio_task_handler which:
    # 1. Calls write_audio_frame
    # 2. Handles bot speaking events through _handle_bot_speech
    # 3. Breaks out after consecutive failures (the bug - doesn't call _bot_stopped_speaking)
    transport = MockTransport(
        generate_audio=False,  # No input audio for this test
        audio_write_succeeds=False,  # Enable write failure mode
        fail_after_n_frames=fail_after_n_frames,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
            # Use faster failure detection for tests
            audio_out_max_consecutive_failures=2,
            audio_out_sleep_between_failures=0.25,
        ),
    )

    # Create LLM context
    context = LLMContext()

    # Create PipecatEngine
    engine = PipecatEngine(
        llm=mock_llm,
        context=context,
        workflow=workflow,
        call_context_vars={"customer_name": "Test User"},
        workflow_run_id=1,
    )

    # Create user mute strategies
    user_mute_strategies = [
        MuteUntilFirstBotCompleteUserMuteStrategy(),
        CallbackUserMuteStrategy(should_mute_callback=engine.should_mute_user),
    ]

    user_params = LLMUserAggregatorParams(
        user_mute_strategies=user_mute_strategies,
    )

    assistant_params = LLMAssistantAggregatorParams(expect_stripped_words=True)

    context_aggregator = LLMContextAggregatorPair(
        context, assistant_params=assistant_params, user_params=user_params
    )
    user_context_aggregator = context_aggregator.user()
    assistant_context_aggregator = context_aggregator.assistant()

    # Create the pipeline
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

    return engine, tts, transport, task


class TestTTSPauseWithAudioWriteFailure:
    """Test scenarios where TTS pause_frame_processing interacts with audio write failures."""

    @pytest.mark.asyncio
    async def test_bot_never_started_speaking_write_fails_immediately(
        self, simple_workflow: WorkflowGraph
    ):
        """Test scenario where bot never starts speaking because write fails immediately.

        Scenario:
        1. LLM generates text response
        2. TTS starts generating audio with pause_frame_processing=True
        3. TTS pauses frame processing (waits for BotStoppedSpeakingFrame)
        4. MediaSender tries to write audio, calls _bot_currently_speaking
        5. write_audio_frame returns False immediately
        6. After consecutive failures, _audio_task_handler breaks out
        7. BUG: _bot_stopped_speaking() is NOT called
        8. TTS remains paused, blocking EndFrame
        9. Pipeline hangs on end_call_with_reason

        This test verifies the hang behavior by using a timeout.
        Note: Uses audio_out_max_consecutive_failures=2 for faster test execution.
        """
        # Create LLM response that will trigger TTS
        step_0_chunks = MockLLMService.create_text_chunks(
            "Hello! This is a test message that should cause TTS to pause."
        )

        test_timed_out = False
        mock_steps = [step_0_chunks]
        llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)

        (
            engine,
            tts,
            transport,
            task,
        ) = await create_test_pipeline_with_failing_transport(
            simple_workflow,
            llm,
            fail_after_n_frames=0,  # Fail immediately - bot never starts speaking
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
                with patch.object(
                    VariableExtractionManager,
                    "_perform_extraction",
                    new_callable=AsyncMock,
                    return_value={},
                ):
                    runner = PipelineRunner()

                    async def run_pipeline():
                        await runner.run(task)

                    async def initialize_and_end_call():
                        await asyncio.sleep(0.01)
                        await engine.initialize()

                        # Start LLM generation - this will trigger TTS
                        await engine.llm.queue_frame(LLMContextFrame(engine.context))

                        # Sleep so that processing is paused in TTS Service
                        await asyncio.sleep(0.1)

                        await engine.end_call_with_reason(
                            EndTaskReason.USER_HANGUP.value,
                            abort_immediately=False,
                        )

                    # Create tasks explicitly for better control
                    pipeline_task = asyncio.create_task(run_pipeline())
                    end_call_task = asyncio.create_task(initialize_and_end_call())

                    # Wait with timeout
                    done, pending = await asyncio.wait(
                        [pipeline_task, end_call_task],
                        timeout=3.0,
                        return_when=asyncio.ALL_COMPLETED,
                    )

                    # If there are pending tasks, we timed out
                    if pending:
                        test_timed_out = True
                        # Cancel all pending tasks
                        for t in pending:
                            t.cancel()

                        # Give limited time for cleanup
                        try:
                            await asyncio.wait_for(
                                asyncio.gather(*pending, return_exceptions=True),
                                timeout=1.0,
                            )
                        except asyncio.TimeoutError:
                            pass  # Cleanup took too long, continue anyway

        # Verify audio write was attempted but failed
        output_transport = transport._output
        assert output_transport._write_attempts > 0, (
            "Audio write should have been attempted"
        )
        assert output_transport._frames_written == 0, (
            "No frames should have been written successfully"
        )

        assert test_timed_out is False, (
            "Test timed out - pipeline hung due to TTS being paused. "
            "BotStoppedSpeakingFrame was not sent before CancelTaskFrame."
        )

    @pytest.mark.asyncio
    async def test_bot_started_speaking_then_write_fails(
        self, simple_workflow: WorkflowGraph
    ):
        """Test scenario where bot starts speaking, then audio write fails mid-stream.

        This tests a more realistic scenario where the transport starts working
        but then encounters issues (e.g., client disconnect mid-stream).

        Scenario:
        1. LLM generates text response
        2. TTS starts generating audio with pause_frame_processing=True
        3. First N audio frames are written successfully
        4. BotStartedSpeakingFrame is emitted
        5. Subsequent writes start failing
        6. After consecutive failures, _audio_task_handler breaks out
        7. BUG: _bot_stopped_speaking() is NOT called
        8. TTS remains paused, blocking EndFrame

        Note: Uses audio_out_max_consecutive_failures=2 for faster test execution.
        """
        step_0_chunks = MockLLMService.create_text_chunks(
            "This is a longer message to ensure multiple audio frames are generated."
        )

        test_timed_out = False
        mock_steps = [step_0_chunks]
        llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)

        # Allow first 3 frames to succeed, then fail
        # This simulates bot starting to speak, then transport disconnecting
        (
            engine,
            tts,
            transport,
            task,
        ) = await create_test_pipeline_with_failing_transport(
            simple_workflow,
            llm,
            fail_after_n_frames=3,  # Bot starts speaking, then fails
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
                with patch.object(
                    VariableExtractionManager,
                    "_perform_extraction",
                    new_callable=AsyncMock,
                    return_value={},
                ):
                    runner = PipelineRunner()

                    async def run_pipeline():
                        await runner.run(task)

                    async def initialize_and_observe():
                        await asyncio.sleep(0.01)
                        await engine.initialize()

                        await engine.llm.queue_frame(LLMContextFrame(engine.context))

                        # Sleep so that processing is paused in TTS Service
                        await asyncio.sleep(0.1)

                        await engine.end_call_with_reason(
                            EndTaskReason.USER_HANGUP.value,
                            abort_immediately=False,
                        )

                    # Create tasks explicitly for better control
                    pipeline_task = asyncio.create_task(run_pipeline())
                    end_call_task = asyncio.create_task(initialize_and_observe())

                    # Wait with timeout
                    done, pending = await asyncio.wait(
                        [pipeline_task, end_call_task],
                        timeout=3.0,
                        return_when=asyncio.ALL_COMPLETED,
                    )

                    # If there are pending tasks, we timed out
                    if pending:
                        test_timed_out = True
                        # Cancel all pending tasks
                        for t in pending:
                            t.cancel()

                        # Give limited time for cleanup
                        try:
                            await asyncio.wait_for(
                                asyncio.gather(*pending, return_exceptions=True),
                                timeout=1.0,
                            )
                        except asyncio.TimeoutError:
                            pass  # Cleanup took too long, continue anyway

        # Verify some frames were written successfully before failure
        output_transport = transport._output
        assert output_transport._frames_written == 3, (
            f"Expected 3 successful writes, got {output_transport._frames_written}"
        )
        assert output_transport._write_attempts > 3, (
            "Should have attempted more writes after initial successes"
        )

        assert test_timed_out is False, (
            "Test timed out - pipeline hung due to TTS being paused. "
            "BotStoppedSpeakingFrame was not sent before CancelTaskFrame."
        )
