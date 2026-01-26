"""Tests for verifying context is updated before next LLM completion during node transitions.

This module tests that when the LLM calls a node transition function, the context is
properly updated with the function call result BEFORE the next LLM completion is triggered.

The key behavior being tested:
1. LLM calls a transition function (e.g., "collect_info")
2. The function result is added to the context
3. The new node's system prompt is set
4. Only THEN is the next LLM completion triggered

This ensures proper conversation flow where the LLM sees its previous tool call
result in the context when generating the next response.
"""

import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.workflow import WorkflowGraph
from api.tests.conftest import (
    AGENT_SYSTEM_PROMPT,
    END_CALL_SYSTEM_PROMPT,
    START_CALL_SYSTEM_PROMPT,
)
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


class ContextCapturingMockLLM(MockLLMService):
    """A MockLLMService that captures the context state at each generation.

    This allows us to verify that tool call results are present in the context
    when the next LLM generation is triggered.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.captured_contexts: List[Dict[str, Any]] = []

    async def _stream_chat_completions_universal_context(self, context):
        """Override to capture context state before streaming chunks."""
        # Deep copy the messages to avoid mutation issues
        messages_snapshot = []
        for msg in context.messages:
            msg_copy = dict(msg)
            # Copy content to avoid reference issues
            if "content" in msg_copy:
                msg_copy["content"] = (
                    str(msg_copy["content"]) if msg_copy["content"] else None
                )
            messages_snapshot.append(msg_copy)

        self.captured_contexts.append(
            {
                "step": self._current_step,
                "messages": messages_snapshot,
                "system_prompt": messages_snapshot[0]["content"]
                if messages_snapshot
                else None,
            }
        )

        # Call parent implementation to stream the mock chunks
        return await super()._stream_chat_completions_universal_context(context)

    def get_context_at_step(self, step: int) -> Dict[str, Any]:
        """Get the captured context at a specific step (0-indexed)."""
        for ctx in self.captured_contexts:
            if ctx["step"] == step:
                return ctx
        return None

    def has_tool_call_result_at_step(self, step: int, function_name: str) -> bool:
        """Check if a tool call result for the given function exists in context at step."""
        ctx = self.get_context_at_step(step)
        if not ctx:
            return False

        for msg in ctx["messages"]:
            # Check for tool/function role messages
            if msg.get("role") == "tool" and msg.get("name") == function_name:
                return True
            # Also check for tool_call_id which indicates a tool response
            if msg.get("tool_call_id") and function_name in str(msg.get("name", "")):
                return True

        return False

    def get_system_prompt_at_step(self, step: int) -> str:
        """Get the system prompt from context at a specific step."""
        ctx = self.get_context_at_step(step)
        if ctx and ctx["messages"]:
            first_msg = ctx["messages"][0]
            if first_msg.get("role") == "system":
                return first_msg.get("content", "")
        return ""


async def run_pipeline_and_capture_context(
    workflow: WorkflowGraph,
    mock_steps: List[List],
    set_node_delay: float = 0.0,
) -> tuple[ContextCapturingMockLLM, LLMContext]:
    """Run a pipeline with context-capturing mock LLM.

    Args:
        workflow: The workflow graph to use.
        mock_steps: List of chunk lists for each LLM generation step.
        set_node_delay: Optional delay (in seconds) to introduce in set_node
            to simulate the race condition where on_context_updated runs slowly.

    Returns:
        Tuple of (ContextCapturingMockLLM, LLMContext) for assertions.
    """
    # Create our context-capturing LLM
    llm = ContextCapturingMockLLM(mock_steps=mock_steps, chunk_delay=0.001)

    # Create MockTTSService
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

    # Wrap set_node with a delay to simulate slow on_context_updated
    if set_node_delay > 0:
        original_set_node = engine.set_node

        async def delayed_set_node(node_id: str):
            await asyncio.sleep(set_node_delay)
            await original_set_node(node_id)

        engine.set_node = delayed_set_node

    # Create the pipeline
    pipeline = Pipeline(
        [
            llm,
            tts,
            mock_transport.output(),
            assistant_context_aggregator,
        ]
    )

    # Create pipeline task
    task = PipelineTask(
        pipeline, params=PipelineParams(allow_interruptions=False), enable_rtvi=False
    )

    engine.set_task(task)

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
                await engine.llm.queue_frame(LLMContextFrame(engine.context))

            await asyncio.gather(run_pipeline(), initialize_engine())

    return llm, context


class TestContextUpdateBeforeNextCompletion:
    """Test that context is properly updated before the next LLM completion."""

    @pytest.mark.asyncio
    async def test_single_transition_updates_context_before_next_completion(
        self, three_node_workflow: WorkflowGraph
    ):
        """Test that a single transition function call updates context before next LLM generation.

        Scenario:
        1. Start node generates response with "collect_info" function call
        2. Engine processes the function call and transitions to agent node
        3. VERIFY: Before agent node's LLM generation, context should have:
           - The tool call result from "collect_info"
           - The agent node's system prompt (not start node's)

        This test introduces a delay in set_node (called by on_context_updated) to simulate
        the race condition where the context frame might reach the LLM before the node
        transition completes. The test verifies the context is still correctly updated.
        """
        # Step 0 (Start node): call collect_info to transition to agent
        step_0_chunks = MockLLMService.create_function_call_chunks(
            function_name="collect_info",
            arguments={},
            tool_call_id="call_transition_1",
        )

        # Step 1 (Agent node): call end_call to transition to end
        step_1_chunks = MockLLMService.create_function_call_chunks(
            function_name="end_call",
            arguments={},
            tool_call_id="call_transition_2",
        )

        # Step 2 (End node): text response (end node has no outgoing edges)
        step_2_chunks = MockLLMService.create_text_chunks("Goodbye!")

        mock_steps = [step_0_chunks, step_1_chunks, step_2_chunks]

        llm, _ = await run_pipeline_and_capture_context(
            workflow=three_node_workflow,
            mock_steps=mock_steps,
            set_node_delay=0.05,  # Introduce 50ms delay in set_node
        )

        # Should have been called 3 times: start node, agent node, end node
        assert llm.get_current_step() == 3, (
            f"Expected 3 LLM generations (start, agent, end), got {llm.get_current_step()}"
        )

        # Verify step 0 (start node) had start node's system prompt
        step_0_prompt = llm.get_system_prompt_at_step(0)
        assert START_CALL_SYSTEM_PROMPT in step_0_prompt, (
            f"Step 0 should have start node prompt, got: {step_0_prompt[:100]}"
        )

        # Verify step 1 (agent node) had:
        # 1. The agent node's system prompt (not start node's)
        step_1_prompt = llm.get_system_prompt_at_step(1)
        assert AGENT_SYSTEM_PROMPT in step_1_prompt, (
            f"Step 1 should have agent node prompt, got: {step_1_prompt[:100]}"
        )
        assert START_CALL_SYSTEM_PROMPT not in step_1_prompt, (
            "Step 1 should NOT have start node prompt anymore"
        )

        # 2. The tool call result from collect_info
        step_1_context = llm.get_context_at_step(1)
        assert step_1_context is not None, "Should have captured context at step 1"

        # Look for the tool response message in the context
        has_tool_response = any(
            msg.get("role") == "tool" or msg.get("tool_call_id")
            for msg in step_1_context["messages"]
        )
        assert has_tool_response, (
            f"Step 1 should have tool response in context. Messages: "
            f"{[m.get('role') for m in step_1_context['messages']]}"
        )

    @pytest.mark.asyncio
    async def test_sequential_transitions_maintain_correct_context(
        self, three_node_workflow: WorkflowGraph
    ):
        """Test that sequential transitions maintain correct context at each step.

        Scenario:
        1. Start node: LLM calls "collect_info" -> transitions to agent
        2. Agent node: LLM calls "end_call" -> transitions to end
        3. Each step should have the correct system prompt and previous tool results

        This test also introduces a delay in set_node to verify the race condition
        is handled correctly.
        """
        # Step 0 (Start node): call collect_info to transition to agent
        step_0_chunks = MockLLMService.create_function_call_chunks(
            function_name="collect_info",
            arguments={},
            tool_call_id="call_transition_1",
        )

        # Step 1 (Agent node): call end_call to transition to end
        step_1_chunks = MockLLMService.create_function_call_chunks(
            function_name="end_call",
            arguments={},
            tool_call_id="call_transition_2",
        )

        # Step 2 (End node): text response
        step_2_chunks = MockLLMService.create_text_chunks("Goodbye!")

        mock_steps = [step_0_chunks, step_1_chunks, step_2_chunks]

        llm, _ = await run_pipeline_and_capture_context(
            workflow=three_node_workflow,
            mock_steps=mock_steps,
            set_node_delay=0.05,  # Introduce 50ms delay in set_node
        )

        # Verify all three nodes were executed
        assert llm.get_current_step() == 3, (
            f"Expected 3 steps, got {llm.get_current_step()}"
        )

        # Step 0: Start node - should have start prompt
        assert START_CALL_SYSTEM_PROMPT in llm.get_system_prompt_at_step(0)

        # Step 1: Agent node - should have agent prompt
        assert AGENT_SYSTEM_PROMPT in llm.get_system_prompt_at_step(1)

        # Step 2: End node - should have end prompt
        assert END_CALL_SYSTEM_PROMPT in llm.get_system_prompt_at_step(2)

        # Verify each subsequent step has the previous tool results
        step_1_ctx = llm.get_context_at_step(1)
        step_2_ctx = llm.get_context_at_step(2)

        # Step 1 should have tool result from collect_info
        step_1_has_tool = any(
            msg.get("role") == "tool" or msg.get("tool_call_id")
            for msg in step_1_ctx["messages"]
        )
        assert step_1_has_tool, "Agent node should see collect_info tool result"

        # Step 2 should have tool results from both transitions
        step_2_tool_messages = [
            msg
            for msg in step_2_ctx["messages"]
            if msg.get("role") == "tool" or msg.get("tool_call_id")
        ]
        assert len(step_2_tool_messages) >= 2, (
            f"End node should see at least 2 tool results, got {len(step_2_tool_messages)}"
        )

    @pytest.mark.asyncio
    async def test_context_messages_preserve_conversation_history(
        self, three_node_workflow: WorkflowGraph
    ):
        """Test that conversation history is preserved across node transitions.

        The context should accumulate:
        - System messages (updated per node)
        - Assistant messages (LLM responses)
        - Tool call messages and results
        """
        # Step 0 (Start node): call collect_info to transition to agent
        step_0_chunks = MockLLMService.create_function_call_chunks(
            function_name="collect_info",
            arguments={},
            tool_call_id="call_transition_1",
        )

        # Step 1 (Agent node): call end_call to transition to end
        step_1_chunks = MockLLMService.create_function_call_chunks(
            function_name="end_call",
            arguments={},
            tool_call_id="call_transition_2",
        )

        # Step 2 (End node): text response
        step_2_chunks = MockLLMService.create_text_chunks("Goodbye!")

        mock_steps = [step_0_chunks, step_1_chunks, step_2_chunks]

        llm, _ = await run_pipeline_and_capture_context(
            workflow=three_node_workflow,
            mock_steps=mock_steps,
        )

        # Get context at each step
        ctx_0 = llm.get_context_at_step(0)
        ctx_1 = llm.get_context_at_step(1)
        ctx_2 = llm.get_context_at_step(2)

        # Message count should increase as conversation progresses
        assert len(ctx_1["messages"]) > len(ctx_0["messages"]), (
            "Context at step 1 should have more messages than step 0"
        )

        assert len(ctx_2["messages"]) > len(ctx_1["messages"]), (
            "Context at step 2 should have more messages than step 1"
        )

        # Verify assistant messages are accumulated
        assistant_messages_at_step_2 = [
            msg for msg in ctx_2["messages"] if msg.get("role") == "assistant"
        ]
        assert len(assistant_messages_at_step_2) >= 2, (
            "Should have at least 2 assistant messages by step 2"
        )
