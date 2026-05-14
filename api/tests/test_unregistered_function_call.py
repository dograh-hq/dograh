"""Tests for LLM behavior when calling an unregistered function."""

import pytest
from pipecat.frames.frames import (
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    FunctionCallsFromLLMInfoFrame,
    FunctionCallsStartedFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext

from pipecat.tests import MockLLMService, run_test


class TestUnregisteredFunctionCall:
    """Tests for LLM behavior when generating a tool call for an unregistered function."""

    @pytest.mark.asyncio
    async def test_unregistered_function_emits_error_result(self):
        """LLM calling an unregistered function should still terminate with a
        FunctionCallResultFrame whose result is an error string, instead of
        crashing the pipeline."""
        chunks = MockLLMService.create_function_call_chunks(
            function_name="nonexistent_tool",
            arguments={"foo": "bar"},
            tool_call_id="call_missing_1",
        )

        llm = MockLLMService(mock_chunks=chunks, chunk_delay=0.001)

        # Intentionally do NOT register any handler for "nonexistent_tool".

        messages = [{"role": "user", "content": "Please use a tool I never registered"}]
        context = LLMContext(messages)

        pipeline = Pipeline([llm])

        received_down_frames, _ = await run_test(
            pipeline,
            frames_to_send=[LLMContextFrame(context)],
            expected_down_frames=[
                LLMFullResponseStartFrame,
                FunctionCallsFromLLMInfoFrame,
                FunctionCallsStartedFrame,
                LLMFullResponseEndFrame,
                FunctionCallInProgressFrame,
                FunctionCallResultFrame,
            ],
        )

        result_frames = [
            f for f in received_down_frames if isinstance(f, FunctionCallResultFrame)
        ]
        assert len(result_frames) == 1, (
            "Expected exactly one FunctionCallResultFrame for the unregistered call"
        )

        result_frame = result_frames[0]
        assert result_frame.function_name == "nonexistent_tool"
        assert result_frame.tool_call_id == "call_missing_1"
        assert result_frame.arguments == {"foo": "bar"}

        # Pipecat's missing-function handler returns a string error.
        assert isinstance(result_frame.result, str)
        assert "not registered" in result_frame.result
        assert "nonexistent_tool" in result_frame.result

        # In-progress frame should also be emitted before the result so mute
        # strategies can release the tool_call_id.
        in_progress_frames = [
            f
            for f in received_down_frames
            if isinstance(f, FunctionCallInProgressFrame)
        ]
        assert len(in_progress_frames) == 1
        assert in_progress_frames[0].function_name == "nonexistent_tool"
        assert in_progress_frames[0].tool_call_id == "call_missing_1"
