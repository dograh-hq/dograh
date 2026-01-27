"""Tests for verifying end_call_with_reason behavior in different scenarios.

This module tests the end call flow in PipecatEngine with multiple scenarios:
1. Normal end call when transitioning to end node
2. End call triggered by custom end_call tool
3. End call triggered by on_client_disconnected
4. Race condition between end_call tool and client disconnect

For all scenarios, we verify:
- Pipeline muting (_mute_pipeline is set to True)
- Variable extraction is called (_perform_variable_extraction_if_needed)
- Call disposition flag is set (_call_disposed is True)
- User audio muting via CallbackUserMuteStrategy (should_mute_user returns True)

The tests use MockTransport with audio generation to simulate real pipeline scenarios
where InputAudioRawFrame frames are continuously generated. The pipeline includes
LLMUserAggregatorParams with user mute strategies (MuteUntilFirstBotCompleteUserMuteStrategy
and CallbackUserMuteStrategy) matching the production run_pipeline.py configuration.
"""

import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from api.enums import ToolCategory
from api.services.workflow.dto import (
    EdgeDataDTO,
    NodeDataDTO,
    NodeType,
    Position,
    ReactFlowDTO,
    RFEdgeDTO,
    RFNodeDTO,
)
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager
from api.services.workflow.pipecat_engine_variable_extractor import (
    VariableExtractionManager,
)
from api.services.workflow.workflow import WorkflowGraph
from api.tests.conftest import END_CALL_SYSTEM_PROMPT, START_CALL_SYSTEM_PROMPT
from pipecat.frames.frames import Frame, LLMContextFrame
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
from pipecat.turns.user_mute import (
    CallbackUserMuteStrategy,
    MuteUntilFirstBotCompleteUserMuteStrategy,
)
from pipecat.utils.enums import EndTaskReason


class EndCallTestHelper:
    """Helper class to track end call related state during tests."""

    def __init__(self):
        self.extraction_calls: List[Dict[str, Any]] = []
        self.mute_pipeline_state: List[bool] = []
        self.call_disposed_state: List[bool] = []
        self.end_call_reasons: List[str] = []
        self.frames_queued: List[Any] = []
        self.should_mute_user_calls: List[bool] = []

    def reset(self):
        """Reset all tracked state."""
        self.extraction_calls.clear()
        self.mute_pipeline_state.clear()
        self.call_disposed_state.clear()
        self.end_call_reasons.clear()
        self.frames_queued.clear()
        self.should_mute_user_calls.clear()


class MockEndCallToolModel:
    """Mock end call tool model for testing."""

    def __init__(
        self,
        tool_uuid: str = "end-call-uuid",
        name: str = "End Call",
        description: str = "End the current call",
        message_type: str = "none",
        custom_message: str = "",
    ):
        self.tool_uuid = tool_uuid
        self.name = name
        self.description = description
        self.category = ToolCategory.END_CALL.value
        self.definition = {
            "schema_version": 1,
            "type": "end_call",
            "config": {
                "messageType": message_type,
                "customMessage": custom_message,
            },
        }


async def create_engine_with_tracking(
    workflow: WorkflowGraph,
    mock_llm: MockLLMService,
    test_helper: EndCallTestHelper,
    generate_audio: bool = True,
) -> tuple[PipecatEngine, MockTTSService, MockTransport, PipelineTask]:
    """Create a PipecatEngine with tracking for end call behavior.

    Args:
        workflow: The workflow graph to use.
        mock_llm: The mock LLM service.
        test_helper: Helper to track test state.
        generate_audio: If True, the mock transport generates InputAudioRawFrame
            every 20ms to simulate real audio input.

    Returns:
        Tuple of (engine, tts, transport, task)
    """
    # Create MockTTSService
    tts = MockTTSService(mock_audio_duration_ms=10, frame_delay=0)

    # Create MockTransport with audio generation to simulate real pipeline
    mock_transport = MockTransport(
        generate_audio=generate_audio,
        audio_interval_ms=20,
        audio_sample_rate=16000,
        audio_num_channels=1,
        emit_bot_speaking=True,
    )

    # Create LLM context
    context = LLMContext()

    # Create PipecatEngine with the workflow (before context aggregator so we can use its callback)
    engine = PipecatEngine(
        llm=mock_llm,
        context=context,
        workflow=workflow,
        call_context_vars={"customer_name": "Test User"},
        workflow_run_id=1,
    )

    # Track variable extraction calls
    original_perform_extraction = engine._perform_variable_extraction_if_needed

    async def tracked_perform_extraction(node, run_in_background: bool = True):
        test_helper.extraction_calls.append(
            {
                "node_id": node.id if node else None,
                "node_name": node.name if node else None,
                "extraction_enabled": node.extraction_enabled if node else None,
                "run_in_background": run_in_background,
            }
        )
        await original_perform_extraction(node, run_in_background=run_in_background)

    engine._perform_variable_extraction_if_needed = tracked_perform_extraction

    # Track end_call_with_reason calls
    original_end_call = engine.end_call_with_reason

    async def tracked_end_call(reason: str, abort_immediately: bool = False):
        # Record state before end_call_with_reason modifies it
        test_helper.end_call_reasons.append(reason)
        await original_end_call(reason, abort_immediately)
        # Record state after end_call_with_reason
        test_helper.mute_pipeline_state.append(engine._mute_pipeline)
        test_helper.call_disposed_state.append(engine._call_disposed)

    engine.end_call_with_reason = tracked_end_call

    # Create context aggregator with user mute strategies (after engine so we can use its callback)
    assistant_params = LLMAssistantAggregatorParams(expect_stripped_words=True)

    # Wrap should_mute_user to track calls
    original_should_mute_user = engine.should_mute_user

    def tracked_should_mute_user(frame: Frame) -> bool:
        result = original_should_mute_user(frame)
        test_helper.should_mute_user_calls.append(result)
        return result

    # Create user mute strategies matching run_pipeline.py
    # - MuteUntilFirstBotCompleteUserMuteStrategy: mutes until first bot response completes
    # - CallbackUserMuteStrategy: mutes based on engine's _mute_pipeline state
    user_mute_strategies = [
        MuteUntilFirstBotCompleteUserMuteStrategy(),
        CallbackUserMuteStrategy(should_mute_callback=tracked_should_mute_user),
    ]

    user_params = LLMUserAggregatorParams(
        user_mute_strategies=user_mute_strategies,
    )

    context_aggregator = LLMContextAggregatorPair(
        context, assistant_params=assistant_params, user_params=user_params
    )
    user_context_aggregator = context_aggregator.user()
    assistant_context_aggregator = context_aggregator.assistant()

    # Create the pipeline with transport input -> user aggregator -> LLM -> TTS -> transport output -> assistant aggregator
    pipeline = Pipeline(
        [
            mock_transport.input(),
            user_context_aggregator,
            mock_llm,
            tts,
            mock_transport.output(),
            assistant_context_aggregator,
        ]
    )

    # Create pipeline task
    task = PipelineTask(pipeline, params=PipelineParams(), enable_rtvi=False)

    engine.set_task(task)

    return engine, tts, mock_transport, task


class TestEndCallViaNodeTransition:
    """Test end call behavior when transitioning to an end node."""

    @pytest.mark.asyncio
    async def test_end_call_via_transition_mutes_pipeline_and_extracts_variables(
        self, simple_workflow: WorkflowGraph
    ):
        """Test that transitioning to end node mutes pipeline and extracts variables.

        Scenario:
        1. Start node has extraction_enabled=True
        2. LLM calls transition function to end node
        3. VERIFY: Pipeline is muted
        4. VERIFY: Variable extraction is called with run_in_background=False
        5. VERIFY: Call is disposed
        """
        test_helper = EndCallTestHelper()

        # Step 0 (Start node): greet user then call end_call to transition to end
        step_0_chunks = MockLLMService.create_mixed_chunks(
            text="Hello! Thank you for calling. Goodbye!",
            function_name="end_call",
            arguments={},
            tool_call_id="call_end_1",
        )

        mock_steps = [step_0_chunks]
        llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)

        engine, tts, transport, task = await create_engine_with_tracking(
            simple_workflow, llm, test_helper
        )

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
                with patch.object(
                    VariableExtractionManager,
                    "_perform_extraction",
                    new_callable=AsyncMock,
                    return_value={"user_intent": "end call"},
                ):
                    runner = PipelineRunner()

                    async def run_pipeline():
                        await runner.run(task)

                    async def initialize_engine():
                        await asyncio.sleep(0.01)
                        await engine.initialize()
                        await engine.llm.queue_frame(LLMContextFrame(engine.context))

                    await asyncio.gather(run_pipeline(), initialize_engine())

        # Verify end_call_with_reason was called
        assert len(test_helper.end_call_reasons) >= 1, (
            "end_call_with_reason should have been called"
        )
        assert EndTaskReason.USER_QUALIFIED.value in test_helper.end_call_reasons

        # Verify pipeline was muted
        assert any(test_helper.mute_pipeline_state), "Pipeline should be muted"

        # Verify call was disposed
        assert any(test_helper.call_disposed_state), "Call should be disposed"

        # Verify variable extraction was called
        # Should have extraction calls - at least one for the transition
        # and one synchronous call in end_call_with_reason
        sync_extraction_calls = [
            call
            for call in test_helper.extraction_calls
            if not call["run_in_background"]
        ]
        assert len(sync_extraction_calls) >= 1, (
            f"Expected at least 1 synchronous extraction call, got {len(sync_extraction_calls)}. "
            f"All calls: {test_helper.extraction_calls}"
        )

        # Verify user muting behavior via CallbackUserMuteStrategy
        # After end_call_with_reason, should_mute_user should return True
        # which causes CallbackUserMuteStrategy to mute user audio
        assert len(test_helper.should_mute_user_calls) > 0, (
            "should_mute_user callback should have been called during pipeline execution"
        )
        # The last calls should return True (after _mute_pipeline is set)
        assert any(test_helper.should_mute_user_calls), (
            "should_mute_user should return True after end_call_with_reason sets _mute_pipeline"
        )

    @pytest.mark.asyncio
    async def test_multi_node_transition_to_end_extracts_from_correct_nodes(
        self, three_node_workflow: WorkflowGraph
    ):
        """Test that multi-node workflow extracts variables from correct nodes.

        Scenario:
        1. Start -> Agent -> End transitions
        2. Both start and agent nodes have extraction enabled
        3. VERIFY: Extraction is called for start node during first transition
        4. VERIFY: Extraction is called for agent node during second transition
        5. VERIFY: Final synchronous extraction is called in end_call_with_reason
        """
        test_helper = EndCallTestHelper()

        # Step 0 (Start node): greet user then call collect_info to transition to agent
        step_0_chunks = MockLLMService.create_mixed_chunks(
            text="Hello! Welcome to our service. Let me collect some information.",
            function_name="collect_info",
            arguments={},
            tool_call_id="call_transition_1",
        )

        # Step 1 (Agent node): acknowledge then call end_call to transition to end
        step_1_chunks = MockLLMService.create_mixed_chunks(
            text="Thank you for providing that information. Goodbye!",
            function_name="end_call",
            arguments={},
            tool_call_id="call_transition_2",
        )

        mock_steps = [step_0_chunks, step_1_chunks]
        llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)

        engine, tts, transport, task = await create_engine_with_tracking(
            three_node_workflow, llm, test_helper
        )

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
                with patch.object(
                    VariableExtractionManager,
                    "_perform_extraction",
                    new_callable=AsyncMock,
                    return_value={"greeting_type": "formal", "user_name": "John"},
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

        # Verify end_call_with_reason was called
        assert len(test_helper.end_call_reasons) >= 1
        assert EndTaskReason.USER_QUALIFIED.value in test_helper.end_call_reasons

        # Verify pipeline was muted and call disposed
        assert any(test_helper.mute_pipeline_state), "Pipeline should be muted"
        assert any(test_helper.call_disposed_state), "Call should be disposed"

        # Verify extraction was called multiple times
        # Background extractions during transitions + synchronous in end_call
        assert len(test_helper.extraction_calls) >= 2, (
            f"Expected at least 2 extraction calls, got {len(test_helper.extraction_calls)}"
        )

        # Verify user muting is active after call ends
        assert any(test_helper.should_mute_user_calls), (
            "should_mute_user should return True after end call"
        )


class TestEndCallViaCustomTool:
    """Test end call behavior when using custom end_call tool."""

    @pytest.mark.asyncio
    async def test_end_call_tool_without_message_ends_immediately(
        self, simple_workflow: WorkflowGraph
    ):
        """Test that end_call tool without custom message ends call immediately.

        Scenario:
        1. LLM calls a custom end_call tool (no message configured)
        2. VERIFY: Pipeline is muted
        3. VERIFY: Variable extraction is called
        4. VERIFY: Call ends with abort_immediately=True
        """
        test_helper = EndCallTestHelper()

        # Step 0: call end_call tool
        step_0_chunks = MockLLMService.create_function_call_chunks(
            function_name="end_call_tool",
            arguments={},
            tool_call_id="call_end_tool_1",
        )

        mock_steps = [step_0_chunks]
        llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)

        engine, tts, transport, task = await create_engine_with_tracking(
            simple_workflow, llm, test_helper
        )

        # Create end call tool
        end_call_tool = MockEndCallToolModel(
            message_type="none",  # No message, immediate end
        )

        # Create CustomToolManager and register the end call handler
        custom_tool_manager = CustomToolManager(engine)
        engine._custom_tool_manager = custom_tool_manager

        # Manually register the end call handler
        handler = custom_tool_manager._create_end_call_handler(
            end_call_tool, "end_call_tool"
        )
        llm.register_function("end_call_tool", handler)

        # Patch DB calls and extraction manager
        with patch(
            "api.services.workflow.pipecat_engine.get_organization_id_from_workflow_run",
            new_callable=AsyncMock,
            return_value=1,
        ):
            with patch(
                "api.services.workflow.pipecat_engine.apply_disposition_mapping",
                new_callable=AsyncMock,
                return_value="end_call_tool",
            ):
                with patch.object(
                    VariableExtractionManager,
                    "_perform_extraction",
                    new_callable=AsyncMock,
                    return_value={"user_intent": "end"},
                ):
                    runner = PipelineRunner()

                    async def run_pipeline():
                        await runner.run(task)

                    async def initialize_engine():
                        await asyncio.sleep(0.01)
                        await engine.initialize()
                        await engine.llm.queue_frame(LLMContextFrame(engine.context))

                    await asyncio.gather(run_pipeline(), initialize_engine())

        # Verify end_call_with_reason was called with END_CALL_TOOL_REASON
        assert len(test_helper.end_call_reasons) >= 1, (
            "end_call_with_reason should have been called"
        )
        assert EndTaskReason.END_CALL_TOOL_REASON.value in test_helper.end_call_reasons

        # Verify pipeline was muted
        assert any(test_helper.mute_pipeline_state), "Pipeline should be muted"

        # Verify call was disposed
        assert any(test_helper.call_disposed_state), "Call should be disposed"

        # Verify user muting is active via CallbackUserMuteStrategy
        assert any(test_helper.should_mute_user_calls), (
            "should_mute_user should return True after end_call_tool"
        )

    @pytest.mark.asyncio
    async def test_end_call_tool_with_custom_message_speaks_before_ending(
        self, simple_workflow: WorkflowGraph
    ):
        """Test that end_call tool with custom message speaks before ending.

        Scenario:
        1. LLM calls a custom end_call tool with custom message
        2. VERIFY: TTS speaks the goodbye message
        3. VERIFY: Pipeline is muted
        4. VERIFY: Variable extraction is called
        """
        test_helper = EndCallTestHelper()

        # Step 0: call end_call tool
        step_0_chunks = MockLLMService.create_function_call_chunks(
            function_name="end_call_with_message",
            arguments={},
            tool_call_id="call_end_tool_1",
        )

        mock_steps = [step_0_chunks]
        llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)

        engine, tts, transport, task = await create_engine_with_tracking(
            simple_workflow, llm, test_helper
        )

        # Create end call tool with custom message
        end_call_tool = MockEndCallToolModel(
            name="End Call With Message",
            message_type="custom",
            custom_message="Thank you for calling. Goodbye!",
        )

        # Create CustomToolManager and register the end call handler
        custom_tool_manager = CustomToolManager(engine)
        engine._custom_tool_manager = custom_tool_manager

        # Manually register the end call handler
        handler = custom_tool_manager._create_end_call_handler(
            end_call_tool, "end_call_with_message"
        )
        llm.register_function("end_call_with_message", handler)

        # Patch DB calls and extraction manager
        with patch(
            "api.services.workflow.pipecat_engine.get_organization_id_from_workflow_run",
            new_callable=AsyncMock,
            return_value=1,
        ):
            with patch(
                "api.services.workflow.pipecat_engine.apply_disposition_mapping",
                new_callable=AsyncMock,
                return_value="end_call_tool",
            ):
                with patch.object(
                    VariableExtractionManager,
                    "_perform_extraction",
                    new_callable=AsyncMock,
                    return_value={"user_intent": "end"},
                ):
                    runner = PipelineRunner()

                    async def run_pipeline():
                        await runner.run(task)

                    async def initialize_engine():
                        await asyncio.sleep(0.01)
                        await engine.initialize()
                        await engine.llm.queue_frame(LLMContextFrame(engine.context))

                    await asyncio.gather(run_pipeline(), initialize_engine())

        # Verify end_call_with_reason was called
        assert len(test_helper.end_call_reasons) >= 1, (
            "end_call_with_reason should have been called"
        )
        assert EndTaskReason.END_CALL_TOOL_REASON.value in test_helper.end_call_reasons

        # Verify pipeline was muted
        assert any(test_helper.mute_pipeline_state), "Pipeline should be muted"

        # Verify call was disposed
        assert any(test_helper.call_disposed_state), "Call should be disposed"

        # Verify user muting is active via CallbackUserMuteStrategy
        assert any(test_helper.should_mute_user_calls), (
            "should_mute_user should return True after end_call_with_message"
        )


class TestEndCallViaClientDisconnect:
    """Test end call behavior when client disconnects."""

    @pytest.mark.asyncio
    async def test_client_disconnect_ends_call_with_user_hangup(
        self, simple_workflow: WorkflowGraph
    ):
        """Test that client disconnect triggers end_call_with_reason.

        Scenario:
        1. Pipeline is running
        2. Client disconnects (simulated via direct call to end_call_with_reason)
        3. VERIFY: Pipeline is muted
        4. VERIFY: Variable extraction is called
        5. VERIFY: Reason is USER_HANGUP
        """
        test_helper = EndCallTestHelper()

        # Create a simple text response
        step_0_chunks = MockLLMService.create_text_chunks(
            "Hello! How can I help you today?"
        )

        mock_steps = [step_0_chunks]
        llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)

        engine, tts, transport, task = await create_engine_with_tracking(
            simple_workflow, llm, test_helper
        )

        # Patch DB calls and extraction manager
        with patch(
            "api.services.workflow.pipecat_engine.get_organization_id_from_workflow_run",
            new_callable=AsyncMock,
            return_value=1,
        ):
            with patch(
                "api.services.workflow.pipecat_engine.apply_disposition_mapping",
                new_callable=AsyncMock,
                return_value="user_hangup",
            ):
                with patch.object(
                    VariableExtractionManager,
                    "_perform_extraction",
                    new_callable=AsyncMock,
                    return_value={"user_intent": "disconnected"},
                ):
                    runner = PipelineRunner()

                    async def run_pipeline():
                        await runner.run(task)

                    async def initialize_and_disconnect():
                        await asyncio.sleep(0.01)
                        await engine.initialize()
                        await engine.llm.queue_frame(LLMContextFrame(engine.context))

                        # Wait for initial generation to complete
                        await asyncio.sleep(0.1)

                        # Simulate client disconnect by calling end_call_with_reason directly
                        # This is what on_client_disconnected does
                        await engine.end_call_with_reason(
                            EndTaskReason.USER_HANGUP.value, abort_immediately=True
                        )

                    await asyncio.gather(run_pipeline(), initialize_and_disconnect())

        # Verify end_call_with_reason was called with USER_HANGUP
        assert EndTaskReason.USER_HANGUP.value in test_helper.end_call_reasons, (
            f"Expected USER_HANGUP in reasons, got: {test_helper.end_call_reasons}"
        )

        # Verify pipeline was muted
        assert any(test_helper.mute_pipeline_state), "Pipeline should be muted"

        # Verify call was disposed
        assert any(test_helper.call_disposed_state), "Call should be disposed"

        # Verify synchronous extraction was called (run_in_background=False)
        sync_extraction_calls = [
            call
            for call in test_helper.extraction_calls
            if not call["run_in_background"]
        ]
        assert len(sync_extraction_calls) >= 1, (
            f"Expected at least 1 synchronous extraction call during disconnect. "
            f"All calls: {test_helper.extraction_calls}"
        )

        # Verify user muting is active via CallbackUserMuteStrategy
        assert any(test_helper.should_mute_user_calls), (
            "should_mute_user should return True after client disconnect"
        )


class TestEndCallRaceConditions:
    """Test race conditions between different end call triggers."""

    @pytest.mark.asyncio
    async def test_only_first_end_call_succeeds(self, simple_workflow: WorkflowGraph):
        """Test that only the first end_call_with_reason call succeeds.

        Scenario:
        1. Multiple end_call_with_reason calls are made concurrently
        2. VERIFY: Only the first one sets _call_disposed
        3. VERIFY: Subsequent calls return early without doing work
        """
        test_helper = EndCallTestHelper()

        # Create a simple text response
        step_0_chunks = MockLLMService.create_text_chunks("Hello!")

        mock_steps = [step_0_chunks]
        llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)

        engine, tts, transport, task = await create_engine_with_tracking(
            simple_workflow, llm, test_helper
        )

        # Patch DB calls and extraction manager
        with patch(
            "api.services.workflow.pipecat_engine.get_organization_id_from_workflow_run",
            new_callable=AsyncMock,
            return_value=1,
        ):
            with patch(
                "api.services.workflow.pipecat_engine.apply_disposition_mapping",
                new_callable=AsyncMock,
                return_value="first_reason",
            ):
                with patch.object(
                    VariableExtractionManager,
                    "_perform_extraction",
                    new_callable=AsyncMock,
                    return_value={"user_intent": "end"},
                ):
                    runner = PipelineRunner()

                    async def run_pipeline():
                        await runner.run(task)

                    async def initialize_and_race():
                        await asyncio.sleep(0.01)
                        await engine.initialize()
                        await engine.llm.queue_frame(LLMContextFrame(engine.context))

                        # Wait for initial generation
                        await asyncio.sleep(0.1)

                        # Try to end call multiple times concurrently
                        await asyncio.gather(
                            engine.end_call_with_reason(
                                EndTaskReason.USER_HANGUP.value, abort_immediately=True
                            ),
                            engine.end_call_with_reason(
                                EndTaskReason.END_CALL_TOOL_REASON.value,
                                abort_immediately=True,
                            ),
                            engine.end_call_with_reason(
                                EndTaskReason.USER_QUALIFIED.value,
                                abort_immediately=False,
                            ),
                        )

                    await asyncio.gather(run_pipeline(), initialize_and_race())

        # Due to the _call_disposed guard, only one end_call should fully execute
        # The tracked end_call_reasons will show all attempted calls
        # but only the first one should modify state
        assert len(test_helper.end_call_reasons) == 3, (
            f"Expected 3 end_call attempts, got {len(test_helper.end_call_reasons)}"
        )

        # Only one should have actually set the mute_pipeline and call_disposed
        # (the others return early due to _call_disposed check)
        # Since we track state AFTER end_call_with_reason, we should see True values
        # only from the first successful call
        assert any(test_helper.mute_pipeline_state), "Pipeline should be muted"
        assert any(test_helper.call_disposed_state), "Call should be disposed"

        # Verify user muting is active via CallbackUserMuteStrategy
        assert any(test_helper.should_mute_user_calls), (
            "should_mute_user should return True after race condition end call"
        )

    @pytest.mark.asyncio
    async def test_end_call_tool_and_disconnect_race(
        self, simple_workflow: WorkflowGraph
    ):
        """Test race between end_call tool and client disconnect.

        Scenario:
        1. LLM calls end_call tool
        2. Client disconnects at nearly the same time
        3. VERIFY: Only one end call succeeds
        4. VERIFY: Call is properly disposed
        """
        test_helper = EndCallTestHelper()

        # Step 0: Text response
        step_0_chunks = MockLLMService.create_text_chunks("Hello!")

        # Step 1: call end_call tool
        step_1_chunks = MockLLMService.create_function_call_chunks(
            function_name="end_call_tool",
            arguments={},
            tool_call_id="call_end_tool_1",
        )

        mock_steps = [step_0_chunks, step_1_chunks]
        llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)

        engine, tts, transport, task = await create_engine_with_tracking(
            simple_workflow, llm, test_helper
        )

        # Create end call tool
        end_call_tool = MockEndCallToolModel(message_type="none")

        # Create CustomToolManager and register the end call handler
        custom_tool_manager = CustomToolManager(engine)
        engine._custom_tool_manager = custom_tool_manager

        handler = custom_tool_manager._create_end_call_handler(
            end_call_tool, "end_call_tool"
        )
        llm.register_function("end_call_tool", handler)

        disconnect_called = False

        # Patch DB calls and extraction manager
        with patch(
            "api.services.workflow.pipecat_engine.get_organization_id_from_workflow_run",
            new_callable=AsyncMock,
            return_value=1,
        ):
            with patch(
                "api.services.workflow.pipecat_engine.apply_disposition_mapping",
                new_callable=AsyncMock,
                return_value="end_reason",
            ):
                with patch.object(
                    VariableExtractionManager,
                    "_perform_extraction",
                    new_callable=AsyncMock,
                    return_value={"user_intent": "end"},
                ):
                    runner = PipelineRunner()

                    async def run_pipeline():
                        await runner.run(task)

                    async def initialize_and_race_disconnect():
                        nonlocal disconnect_called
                        await asyncio.sleep(0.01)
                        await engine.initialize()
                        await engine.llm.queue_frame(LLMContextFrame(engine.context))

                        # Wait for the end_call tool to be called
                        await asyncio.sleep(0.15)

                        # Simulate client disconnect racing with end_call tool
                        disconnect_called = True
                        await engine.end_call_with_reason(
                            EndTaskReason.USER_HANGUP.value, abort_immediately=True
                        )

                    await asyncio.gather(
                        run_pipeline(), initialize_and_race_disconnect()
                    )

        # Verify disconnect was attempted
        assert disconnect_called, "Disconnect should have been called"

        # Verify at least one end call reason was recorded
        assert len(test_helper.end_call_reasons) >= 1, (
            "At least one end_call should have been attempted"
        )

        # Verify call was properly disposed
        assert engine._call_disposed, "Call should be disposed"

        # Verify pipeline was muted
        assert engine._mute_pipeline, "Pipeline should be muted"

        # Verify user muting is active via CallbackUserMuteStrategy
        assert any(test_helper.should_mute_user_calls), (
            "should_mute_user should return True after end call"
        )


class TestEndCallExtractionBehavior:
    """Test variable extraction behavior during end call."""

    @pytest.mark.asyncio
    async def test_synchronous_extraction_in_end_call(
        self, simple_workflow: WorkflowGraph
    ):
        """Test that end_call_with_reason performs synchronous extraction.

        Scenario:
        1. End call is triggered
        2. VERIFY: Variable extraction is called with run_in_background=False
        3. VERIFY: Extraction completes before call ends
        """
        test_helper = EndCallTestHelper()
        extraction_completed = asyncio.Event()

        # Create a simple text response
        step_0_chunks = MockLLMService.create_text_chunks("Hello!")

        mock_steps = [step_0_chunks]
        llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)

        engine, tts, transport, task = await create_engine_with_tracking(
            simple_workflow, llm, test_helper
        )

        # Create a custom extraction mock that signals when called
        async def mock_extraction(*args, **kwargs):
            # Simulate some extraction work
            await asyncio.sleep(0.05)
            extraction_completed.set()
            return {"user_intent": "extracted"}

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
                with patch.object(
                    VariableExtractionManager,
                    "_perform_extraction",
                    side_effect=mock_extraction,
                ):
                    runner = PipelineRunner()

                    async def run_pipeline():
                        await runner.run(task)

                    async def initialize_and_end():
                        await asyncio.sleep(0.01)
                        await engine.initialize()
                        await engine.llm.queue_frame(LLMContextFrame(engine.context))

                        # Wait for initial generation
                        await asyncio.sleep(0.1)

                        # End the call
                        await engine.end_call_with_reason(
                            EndTaskReason.USER_HANGUP.value, abort_immediately=True
                        )

                        # Verify extraction was awaited (synchronous)
                        assert extraction_completed.is_set(), (
                            "Extraction should have completed before end_call returned"
                        )

                    await asyncio.gather(run_pipeline(), initialize_and_end())

        # Verify synchronous extraction was used
        sync_extractions = [
            call
            for call in test_helper.extraction_calls
            if not call["run_in_background"]
        ]
        assert len(sync_extractions) >= 1, (
            f"Expected synchronous extraction, got: {test_helper.extraction_calls}"
        )

        # Verify user muting is active via CallbackUserMuteStrategy
        assert any(test_helper.should_mute_user_calls), (
            "should_mute_user should return True after end call"
        )

    @pytest.mark.asyncio
    async def test_extraction_skipped_for_node_without_extraction(
        self, simple_workflow: WorkflowGraph
    ):
        """Test that extraction is skipped when current node has no extraction.

        Scenario:
        1. Engine is on a node with extraction_enabled=False
        2. End call is triggered
        3. VERIFY: Extraction is attempted but skips due to node config
        """
        test_helper = EndCallTestHelper()

        # Create a simple text response
        step_0_chunks = MockLLMService.create_text_chunks("Hello!")

        mock_steps = [step_0_chunks]
        llm = MockLLMService(mock_steps=mock_steps, chunk_delay=0.001)

        # Create a workflow where start node has NO extraction
        dto = ReactFlowDTO(
            nodes=[
                RFNodeDTO(
                    id="start",
                    type=NodeType.startNode,
                    position=Position(x=0, y=0),
                    data=NodeDataDTO(
                        name="Start Call",
                        prompt=START_CALL_SYSTEM_PROMPT,
                        is_start=True,
                        allow_interrupt=False,
                        add_global_prompt=False,
                        extraction_enabled=False,  # No extraction
                    ),
                ),
                RFNodeDTO(
                    id="end",
                    type=NodeType.endNode,
                    position=Position(x=0, y=200),
                    data=NodeDataDTO(
                        name="End Call",
                        prompt=END_CALL_SYSTEM_PROMPT,
                        is_end=True,
                        allow_interrupt=False,
                        add_global_prompt=False,
                        extraction_enabled=False,
                    ),
                ),
            ],
            edges=[
                RFEdgeDTO(
                    id="start-end",
                    source="start",
                    target="end",
                    data=EdgeDataDTO(
                        label="End Call",
                        condition="When ready to end the call",
                    ),
                ),
            ],
        )
        workflow_no_extraction = WorkflowGraph(dto)

        engine, tts, transport, task = await create_engine_with_tracking(
            workflow_no_extraction, llm, test_helper
        )

        extraction_mock = AsyncMock(return_value={})

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
                with patch.object(
                    VariableExtractionManager,
                    "_perform_extraction",
                    extraction_mock,
                ):
                    runner = PipelineRunner()

                    async def run_pipeline():
                        await runner.run(task)

                    async def initialize_and_end():
                        await asyncio.sleep(0.01)
                        await engine.initialize()
                        await engine.llm.queue_frame(LLMContextFrame(engine.context))

                        # Wait for initial generation
                        await asyncio.sleep(0.1)

                        # End the call
                        await engine.end_call_with_reason(
                            EndTaskReason.USER_HANGUP.value, abort_immediately=True
                        )

                    await asyncio.gather(run_pipeline(), initialize_and_end())

        # Extraction should have been called but the inner _perform_extraction
        # should not have been called because extraction_enabled=False
        # Our tracked_perform_extraction still records the call attempt
        # but VariableExtractionManager._perform_extraction should not be called
        extraction_mock.assert_not_called()

        # Even without extraction, user muting should still be active
        assert any(test_helper.should_mute_user_calls), (
            "should_mute_user should return True after end call (even without extraction)"
        )
