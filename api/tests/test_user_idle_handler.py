"""
Simulates a user idle condition and tests the behaviour
of the user idle handler.

This module tests the behavior when the user becomes idle during a conversation,
ensuring the user_idle_timeout in LLMUserAggregatorParams properly triggers
the on_user_turn_idle event and the engine handles it correctly.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.pipecat_engine import PipecatEngine
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


async def run_pipeline_with_user_idle(
    workflow: WorkflowGraph,
    user_idle_timeout: float = 0.2,
    mock_steps: list | None = None,
) -> tuple[MockLLMService, LLMContext]:
    """Run a pipeline with user_idle_timeout and simulate user idle condition.

    Args:
        workflow: The workflow graph to use.
        user_idle_timeout: Timeout in seconds before considering user idle.
        mock_steps: Optional list of mock step chunks for the LLM. If not provided,
            defaults to a simple greeting followed by text responses.

    Returns:
        Tuple of (MockLLMService, LLMContext) for assertions.
    """
    # Create mock responses - bot will speak first, then respond to idle prompts
    # Step 1: Initial greeting
    # Step 2: Response to first idle (asking if user is still there)
    # Step 3: Response to second idle (goodbye message)

    if mock_steps is None:
        mock_steps = MockLLMService.create_multi_step_responses(
            MockLLMService.create_text_chunks("Hello, how can I help you today?"),
            num_text_steps=4,  # Initial + 2 idle responses + 1 variable extraction
            step_prefix="Response",
        )

    llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)
    tts = MockTTSService(mock_audio_duration_ms=10)

    # Create MockTransport for simulating transport behavior
    mock_transport = MockTransport(emit_bot_speaking=True)

    # Create LLM context
    context = LLMContext()

    # Create context aggregator with user_idle_timeout in user_params
    assistant_params = LLMAssistantAggregatorParams(expect_stripped_words=True)
    user_params = LLMUserAggregatorParams(user_idle_timeout=user_idle_timeout)
    context_aggregator = LLMContextAggregatorPair(
        context, assistant_params=assistant_params, user_params=user_params
    )
    user_context_aggregator = context_aggregator.user()
    assistant_context_aggregator = context_aggregator.assistant()

    # Create PipecatEngine with the workflow
    engine = PipecatEngine(
        llm=llm,
        context=context,
        workflow=workflow,
        call_context_vars={"customer_name": "Test User"},
        workflow_run_id=1,
    )

    # Register user idle event handlers
    user_idle_handler = engine.create_user_idle_handler()

    @user_context_aggregator.event_handler("on_user_turn_idle")
    async def on_user_turn_idle(aggregator):
        await user_idle_handler.handle_idle(aggregator)

    @user_context_aggregator.event_handler("on_user_turn_started")
    async def on_user_turn_started(aggregator, strategy):
        user_idle_handler.reset()

    # Build the pipeline
    pipeline = Pipeline(
        [
            user_context_aggregator,
            llm,
            tts,
            mock_transport.output(),
            assistant_context_aggregator,
        ]
    )

    # Create pipeline task
    task = PipelineTask(pipeline, params=PipelineParams(), enable_rtvi=False)

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

            # Calculate total wait time:
            # - Initial bot speech
            # - First idle timeout (user_idle_timeout)
            # - First idle callback + LLM generation
            # - Second idle timeout (user_idle_timeout)
            # - Second idle callback (ends the task)
            # Add buffer for processing time
            total_wait_time = (user_idle_timeout * 3) + 1.0

            async def wait_for_idle_to_trigger():
                # Wait long enough for idle timeouts to trigger
                await asyncio.sleep(total_wait_time)

            # Run all concurrently
            await asyncio.gather(
                run_pipeline(),
                initialize_engine(),
                wait_for_idle_to_trigger(),
                return_exceptions=True,
            )

    return llm, context


class TestUserIdleHandler:
    """Test user idle handling through PipecatEngine and UserIdleHandler."""

    @pytest.mark.asyncio
    async def test_user_idle_triggers_callback(self, simple_workflow: WorkflowGraph):
        """Test that user idle condition properly triggers the callback.

        This test verifies that when:
        1. The bot starts speaking (triggers conversation tracking)
        2. No user input is received for the timeout period
        3. The on_user_turn_idle event triggers the idle handler

        The engine's user idle handler should:
        - First retry: Send a message asking if user is still there
        - Second retry: Send goodbye message and end the call
        """
        llm, context = await run_pipeline_with_user_idle(
            workflow=simple_workflow,
            user_idle_timeout=0.2,  # Short timeout for faster test
        )

        # Final message in the context should be from the bot
        assert len(context.messages) == 6, "Total 6 messages"
        assert context.messages[-1]["content"] == "Response 2", (
            "Final message in the context should be from LLM"
        )

    @pytest.mark.asyncio
    async def test_user_idle_with_node_transition(
        self, three_node_workflow: WorkflowGraph
    ):
        """Test user idle handling with node transition via tool call.

        This test verifies that when:
        1. The bot starts speaking with initial greeting
        2. The LLM generates a tool call to transition to the next node
        3. The pipeline correctly handles the transition

        The mock steps are:
        - Step 1: Text "Hello, how can I help you today?"
        - Step 2: Tool call "collect_info" to transition to agent node
        - Step 3+: Text responses after transition
        """
        # Create custom mock steps with tool call for node transition
        # For three_node_workflow:
        # - Edge from node 1 -> node 2 has label "Collect Info" -> function: "collect_info"
        # - Edge from node 2 -> node 3 has label "End Call" -> function: "end_call"
        mock_steps = [
            # Step 1: Initial greeting (text)
            MockLLMService.create_text_chunks("Hello, how can I help you today?"),
            # Step 2: Transition to Collect Info node (tool call)
            MockLLMService.create_function_call_chunks(
                function_name="collect_info",
                arguments={},
                tool_call_id="call_collect_info",
            ),
            # Step 3: Response after transition (text)
            MockLLMService.create_text_chunks("Response after transition"),
            # Step 4+: Additional responses for idle handling
            MockLLMService.create_text_chunks("Response 2"),
            MockLLMService.create_text_chunks("Response 3"),
        ]

        llm, context = await run_pipeline_with_user_idle(
            workflow=three_node_workflow,
            user_idle_timeout=0.2,
            mock_steps=mock_steps,
        )

        # Verify the LLM was called multiple times (initial + after transition)
        assert llm.get_current_step() >= 2, (
            "LLM should have been called at least twice (initial + after transition)"
        )

        # This should be the message that we inserted from user_idle_handler
        assert context.messages[2]["role"] == "system", (
            "The system message should be in the context"
        )
        assert "The user has been quiet." in context.messages[2]["content"]
