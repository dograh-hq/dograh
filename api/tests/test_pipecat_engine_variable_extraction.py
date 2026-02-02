"""Tests for verifying variable extraction is triggered for the correct node during transitions.

This module tests that when the LLM calls a node transition function, variable extraction
is performed for the SOURCE node (where the conversation happened), not the TARGET node.

The key behavior being tested:
1. LLM calls a transition function (e.g., "collect_info") from START node
2. START node has extraction_enabled=True with extraction_variables
3. AGENT node (target) has extraction_enabled=False
4. Variable extraction should be triggered for START node's variables
5. Variable extraction should NOT be triggered for AGENT node
"""

import asyncio
from typing import Any, Dict, List
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
)
from pipecat.tests import MockLLMService, MockTTSService
from pipecat.tests.mock_transport import MockTransport
from pipecat.transports.base_transport import TransportParams


class TestVariableExtractionDuringTransitions:
    """Test that variable extraction is triggered for the correct node during transitions."""

    @pytest.mark.asyncio
    async def test_extraction_called_for_source_node_not_target_node(
        self, three_node_workflow_extraction_start_only: WorkflowGraph
    ):
        """Test that when transitioning from START to AGENT, extraction is called for START node.

        Scenario:
        1. Start node has extraction_enabled=True with extraction_variables
        2. Agent node has extraction_enabled=False
        3. LLM calls transition function to move from START to AGENT
        4. VERIFY: Variable extraction should be called for START node's variables
        5. VERIFY: Variable extraction should NOT be called for AGENT node

        This test verifies that extraction happens for the SOURCE node of a transition,
        which is the node where the conversation context that needs extraction occurred.
        """
        # Track which nodes had extraction performed
        extraction_calls: List[Dict[str, Any]] = []

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

        # Create mock LLM
        llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)

        # Create MockTTSService
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

        workflow = three_node_workflow_extraction_start_only

        # Create PipecatEngine with the workflow
        engine = PipecatEngine(
            llm=llm,
            context=context,
            workflow=workflow,
            call_context_vars={"customer_name": "Test User"},
            workflow_run_id=1,
        )

        # Patch _perform_variable_extraction_if_needed to track calls
        original_perform_extraction = engine._perform_variable_extraction_if_needed

        async def tracked_perform_extraction(node, run_in_background=True):
            extraction_calls.append(
                {
                    "node_id": node.id if node else None,
                    "node_name": node.name if node else None,
                    "extraction_enabled": node.extraction_enabled if node else None,
                    "extraction_variables": node.extraction_variables if node else None,
                }
            )
            # Call original to maintain behavior
            await original_perform_extraction(node)

        engine._perform_variable_extraction_if_needed = tracked_perform_extraction

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
            pipeline,
            params=PipelineParams(),
            enable_rtvi=False,
        )

        engine.set_task(task)

        # Patch DB calls and extraction manager
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
                # Mock the actual extraction to avoid needing a real LLM
                with patch.object(
                    VariableExtractionManager,
                    "_perform_extraction",
                    new_callable=AsyncMock,
                    return_value={"user_name": "John Doe"},
                ):
                    runner = PipelineRunner()

                    async def run_pipeline():
                        await runner.run(task)

                    async def initialize_engine():
                        await asyncio.sleep(0.01)
                        await engine.initialize()
                        await engine.llm.queue_frame(LLMContextFrame(engine.context))

                    await asyncio.gather(run_pipeline(), initialize_engine())

        # Should have 3 LLM generations
        assert llm.get_current_step() == 3

        # Verify extraction was called during transitions
        # The key assertion: when transitioning from START to AGENT,
        # the extraction should be for START node (which has extraction enabled)

        # Filter to only calls where extraction was actually attempted
        # (node has extraction_enabled=True and extraction_variables)
        extraction_enabled_calls = [
            call
            for call in extraction_calls
            if call["extraction_enabled"] and call["extraction_variables"]
        ]

        # START node has extraction enabled, so when transitioning FROM start,
        # extraction should be triggered for START's variables
        assert len(extraction_enabled_calls) >= 1, (
            f"Expected at least 1 extraction call for start node, got {len(extraction_enabled_calls)}. "
            f"All calls: {extraction_calls}"
        )

        # Verify the extraction was called for the START node
        start_extraction_calls = [
            call for call in extraction_enabled_calls if call["node_id"] == "start"
        ]
        assert len(start_extraction_calls) >= 1, (
            f"Expected extraction to be called for START node (which has extraction enabled), "
            f"but got calls for: {[c['node_id'] for c in extraction_enabled_calls]}"
        )

        # Verify extraction was NOT called for AGENT node
        agent_extraction_calls = [
            call
            for call in extraction_calls
            if call["node_id"] == "agent" and call["extraction_enabled"]
        ]
        assert len(agent_extraction_calls) == 0, (
            f"Expected NO extraction calls for AGENT node (extraction disabled), "
            f"but got {len(agent_extraction_calls)} calls"
        )
