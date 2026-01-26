"""Tests for tool calls with PipecatEngine and MockLLM.

This module tests the behavior when the LLM generates tool calls (single or parallel),
using PipecatEngine's actual function registration and execution logic.
"""

import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.workflow import WorkflowGraph
from api.tests.conftest import END_CALL_SYSTEM_PROMPT
from pipecat.frames.frames import LLMContextFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response import LLMAssistantAggregatorParams
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.tests import MockLLMService, MockTTSService
from pipecat.tests.mock_transport import MockTransport


async def run_pipeline_with_tool_calls(
    workflow: WorkflowGraph,
    functions: List[Dict[str, Any]],
    text: str | None = None,
    num_text_steps: int = 1,
) -> tuple[MockLLMService, LLMContext]:
    """Run a pipeline with mock tool calls and return the LLM for assertions.

    Args:
        workflow: The workflow graph to use.
        functions: List of function call definitions with name, arguments, and tool_call_id.
        text: Text to add to the first step (streamed before the tool calls).
        num_text_steps: Number of text response steps after the tool calls.

    Returns:
        The MockLLMService instance for making assertions.
    """
    # Create first step chunks
    if text:
        # Create text chunks (without final chunk) followed by function call chunks
        text_chunks = MockLLMService.create_text_chunks(text)
        func_chunks = MockLLMService.create_multiple_function_call_chunks(functions)
        # Exclude the final chunk from text_chunks (which has finish_reason="stop")
        first_step_chunks = text_chunks[:-1] + func_chunks
    else:
        first_step_chunks = MockLLMService.create_multiple_function_call_chunks(
            functions
        )

    # Create multi-step responses
    mock_steps = MockLLMService.create_multi_step_responses(
        first_step_chunks, num_text_steps=num_text_steps, step_prefix="Response"
    )

    # Create MockLLMService with multi-step support
    llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)

    # Create MockTTSService to generate TTS frames
    tts = MockTTSService(mock_audio_duration_ms=10, frame_delay=0)

    # Create MockTransport for simulating transport behavior
    mock_transport = MockTransport(emit_bot_speaking=False)

    # Create LLM context
    context = LLMContext()

    # Add assistant context aggregator
    assistant_params = LLMAssistantAggregatorParams(expect_stripped_words=True)
    context_aggregator = LLMContextAggregatorPair(
        context, assistant_params=assistant_params
    )
    assistant_context_aggregator = context_aggregator.assistant()

    # Create PipecatEngine with the workflow
    engine = PipecatEngine(
        llm=llm,
        context=context,
        workflow=workflow,
        call_context_vars={"customer_name": "Test User"},
        workflow_run_id=1,
    )

    # Create the pipeline with the mock LLM and TTS
    pipeline = Pipeline(
        [
            llm,
            tts,
            mock_transport.output(),
            assistant_context_aggregator,
        ]
    )

    # Create a real pipeline task
    task = PipelineTask(
        pipeline, params=PipelineParams(allow_interruptions=False), enable_rtvi=False
    )

    engine.set_task(task)

    # Patch DB calls to avoid actual database access
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
                # Small delay to let runner start
                await asyncio.sleep(0.01)
                await engine.initialize()
                await engine.llm.queue_frame(LLMContextFrame(engine.context))

            # Run both concurrently
            await asyncio.gather(run_pipeline(), initialize_engine())

    return llm, context


class TestPipecatEngineToolCalls:
    """Test tool calls through PipecatEngine."""

    @pytest.mark.asyncio
    async def test_parallel_builtin_and_transition_calls_through_engine(
        self, simple_workflow: WorkflowGraph
    ):
        """Test parallel function calls using PipecatEngine's actual handlers.

        This test verifies that when the LLM generates parallel tool calls for:
        1. A built-in function (safe_calculator) - registered by _register_builtin_functions
        2. A transition function (end_call) - registered by _register_transition_function_with_llm

        Both functions are properly executed through the engine's handlers and
        the transition correctly moves to the end node.

        The test uses multi-step mock responses:
        - Step 1: Parallel tool calls (safe_calculator + end_call)
        - Step 2+: Text responses for subsequent node prompts
        """
        functions = [
            {
                "name": "end_call",
                "arguments": {},
                "tool_call_id": "call_transition",
            },
            {
                "name": "safe_calculator",
                "arguments": {"expression": "25 * 4"},
                "tool_call_id": "call_calc",
            },
        ]

        llm, context = await run_pipeline_with_tool_calls(
            workflow=simple_workflow,
            functions=functions,
            num_text_steps=2,
        )

        # Assert that the LLM generation was called a total of 2 times,
        # 1st time when StartNode was executed, and second time
        # when EndCall generation happened
        assert llm.get_current_step() == 2, (
            "LLM generation should have happened 2 times"
        )

        # Assert that the context was updated with END_CALL_SYSTEM_PROMPT
        assert context.messages[0]["content"] == END_CALL_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_parallel_builtin_and_transition_calls_through_engine_1(
        self, simple_workflow: WorkflowGraph
    ):
        """Test parallel function calls using PipecatEngine's actual handlers.

        This test verifies that when the LLM generates parallel tool calls for:
        1. A built-in function (safe_calculator) - registered by _register_builtin_functions
        2. A transition function (end_call) - registered by _register_transition_function_with_llm

        Both functions are properly executed through the engine's handlers and
        the transition correctly moves to the end node.

        The test uses multi-step mock responses:
        - Step 1: Parallel tool calls (safe_calculator + end_call)
        - Step 2+: Text responses for subsequent node prompts
        """
        functions = [
            {
                "name": "safe_calculator",
                "arguments": {"expression": "25 * 4"},
                "tool_call_id": "call_calc",
            },
            {
                "name": "end_call",
                "arguments": {},
                "tool_call_id": "call_transition",
            },
        ]

        llm, context = await run_pipeline_with_tool_calls(
            workflow=simple_workflow,
            functions=functions,
            num_text_steps=2,
        )

        # Assert that the LLM generation was called a total of 2 times,
        # 1st time when StartNode was executed, and second time
        # when EndCall generation happened. The tool should not invoke
        # an LLM generation
        assert llm.get_current_step() == 2, (
            "LLM generation should have happened 2 times"
        )

        # Assert that the context was updated with END_CALL_SYSTEM_PROMPT
        assert context.messages[0]["content"] == END_CALL_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_parallel_builtin_and_transition_calls_through_engine_with_text(
        self, simple_workflow: WorkflowGraph
    ):
        """Test parallel function calls using PipecatEngine's actual handlers.

        This test verifies that when the LLM generates parallel tool calls for:
        1. A built-in function (safe_calculator) - registered by _register_builtin_functions
        2. A transition function (end_call) - registered by _register_transition_function_with_llm

        Both functions are properly executed through the engine's handlers and
        the transition correctly moves to the end node.

        The test uses multi-step mock responses:
        - Step 1: Parallel tool calls (safe_calculator + end_call)
        - Step 2+: Text responses for subsequent node prompts
        """
        functions = [
            {
                "name": "end_call",
                "arguments": {},
                "tool_call_id": "call_transition",
            },
            {
                "name": "safe_calculator",
                "arguments": {"expression": "25 * 4"},
                "tool_call_id": "call_calc",
            },
        ]

        llm, context = await run_pipeline_with_tool_calls(
            workflow=simple_workflow,
            functions=functions,
            text="Hello There!",
            num_text_steps=2,
        )

        # Assert that the LLM generation was called a total of 2 times,
        # 1st time when StartNode was executed, and second time
        # when EndCall generation happened. The tool should not invoke
        # an LLM generation
        assert llm.get_current_step() == 2, (
            "LLM generation should have happened 2 times"
        )

        # Assert that the context was updated with END_CALL_SYSTEM_PROMPT
        assert context.messages[0]["content"] == END_CALL_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_single_transition_call_through_engine(
        self, simple_workflow: WorkflowGraph
    ):
        """Test a single transition function call (end_call) through PipecatEngine.

        This test verifies that when the LLM generates only a transition tool call,
        the engine properly executes it and transitions to the end node.
        Since end_call transitions to the end node which triggers another LLM
        generation, the LLM is called exactly once for the initial StartNode.
        """
        functions = [
            {
                "name": "end_call",
                "arguments": {},
                "tool_call_id": "call_transition",
            },
        ]

        llm, context = await run_pipeline_with_tool_calls(
            workflow=simple_workflow,
            functions=functions,
            num_text_steps=1,
        )

        # LLM is called once for the StartNode, then end_call transitions to EndNode
        # which triggers a second generation
        assert llm.get_current_step() == 2, (
            "LLM generation should have happened 2 times"
        )

        # Assert that the context was updated with END_CALL_SYSTEM_PROMPT
        assert context.messages[0]["content"] == END_CALL_SYSTEM_PROMPT
