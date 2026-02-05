"""Tests for tool calls with PipecatEngine and MockLLM.

This module tests the behavior when the LLM generates tool calls (single or parallel),
using PipecatEngine's actual function registration and execution logic.
"""

import asyncio
from typing import Any, Callable, Coroutine, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.transfer_event_protocol import send_transfer_signal
from api.services.workflow.workflow import WorkflowGraph
from api.tests.conftest import END_CALL_SYSTEM_PROMPT, MockToolModel
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
from pipecat.transports.base_transport import TransportParams


async def run_pipeline_with_tool_calls(
    workflow: WorkflowGraph,
    functions: List[Dict[str, Any]],
    text: str | None = None,
    num_text_steps: int = 1,
    mock_tools: Optional[List[MockToolModel]] = None,
    on_engine_ready: Optional[
        Callable[[PipecatEngine], Coroutine[Any, Any, None]]
    ] = None,
) -> tuple[MockLLMService, LLMContext, PipecatEngine]:
    """Run a pipeline with mock tool calls and return the LLM for assertions.

    Args:
        workflow: The workflow graph to use.
        functions: List of function call definitions with name, arguments, and tool_call_id.
        text: Text to add to the first step (streamed before the tool calls).
        num_text_steps: Number of text response steps after the tool calls.
        mock_tools: Optional list of mock tools to be returned by db_client.get_tools_by_uuids.
        on_engine_ready: Optional async callback called after engine is initialized.
            Useful for sending signals or performing actions during pipeline execution.

    Returns:
        Tuple of (MockLLMService, LLMContext, PipecatEngine) for making assertions.
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
    tts = MockTTSService(mock_audio_duration_ms=40, frame_delay=0)

    # Create MockTransport for simulating transport behavior
    mock_transport = MockTransport(
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
        ),
    )

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
        audio_out_sample_rate=16000,
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
    task = PipelineTask(pipeline, params=PipelineParams(), enable_rtvi=False)

    engine.set_task(task)

    # Patch DB calls to avoid actual database access
    with patch(
        "api.services.workflow.pipecat_engine.get_organization_id_from_workflow_run",
        new_callable=AsyncMock,
        return_value=1,
    ):
        with patch(
            "api.services.workflow.pipecat_engine_custom_tools.get_organization_id_from_workflow_run",
            new_callable=AsyncMock,
            return_value=1,
        ):
            with patch(
                "api.services.workflow.pipecat_engine.apply_disposition_mapping",
                new_callable=AsyncMock,
                return_value="completed",
            ):
                with patch(
                    "api.services.workflow.pipecat_engine_custom_tools.db_client.get_tools_by_uuids",
                    new_callable=AsyncMock,
                    return_value=mock_tools or [],
                ):
                    runner = PipelineRunner()

                    async def run_pipeline():
                        await runner.run(task)

                    async def initialize_engine():
                        # Small delay to let runner start
                        await asyncio.sleep(0.01)
                        await engine.initialize()
                        await engine.llm.queue_frame(LLMContextFrame(engine.context))

                    async def run_callback():
                        if on_engine_ready:
                            # Wait for engine to process tool calls
                            await asyncio.sleep(0.1)
                            await on_engine_ready(engine)

                    # Run all concurrently
                    await asyncio.gather(
                        run_pipeline(), initialize_engine(), run_callback()
                    )

    return llm, context, engine


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

        llm, context, _ = await run_pipeline_with_tool_calls(
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

        llm, context, _ = await run_pipeline_with_tool_calls(
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

        llm, context, _ = await run_pipeline_with_tool_calls(
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

        llm, context, _ = await run_pipeline_with_tool_calls(
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

    @pytest.mark.asyncio
    async def test_transfer_call_tool_execution(
        self, simple_workflow: WorkflowGraph, transfer_call_tool: MockToolModel
    ):
        """Test transfer call tool execution through PipecatEngine.

        This test verifies that when the LLM calls the transfer_to_support tool:
        1. The transfer call handler is invoked
        2. The handler waits for a transfer signal via Redis pub/sub
        3. When the signal is sent, the handler proceeds
        4. The gathered_context is updated with transfer_requested=True
        5. The gathered_context contains the transfer_number
        """
        # Add the transfer tool to the start node at runtime
        simple_workflow.nodes["start"].tool_uuids = [transfer_call_tool.tool_uuid]
        simple_workflow.nodes["start"].extraction_enabled = False

        # The function name is derived from the tool name (snake_case)
        functions = [
            {
                "name": "transfer_to_support",
                "arguments": {},
                "tool_call_id": "call_transfer",
            },
        ]

        # Callback to send transfer signal while handler is waiting
        async def send_signal(engine: PipecatEngine):
            # Wait a bit to allow hold music to play
            await asyncio.sleep(0.5)
            # Send the transfer signal to unblock the waiting handler
            await send_transfer_signal(
                workflow_run_id=engine._workflow_run_id,
            )

        _, _, engine = await run_pipeline_with_tool_calls(
            workflow=simple_workflow,
            functions=functions,
            num_text_steps=1,
            mock_tools=[transfer_call_tool],
            on_engine_ready=send_signal,
        )

        # Verify the gathered context was updated with transfer information
        gathered_context = await engine.get_gathered_context()

        assert gathered_context.get("transfer_requested") is True, (
            "transfer_requested should be True in gathered_context"
        )
        assert gathered_context.get("transfer_number") == "+15551234567", (
            "transfer_number should match the configured number"
        )
