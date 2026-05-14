"""Tests verifying user is muted while a transition function is executing.

When the LLM calls a transition function (registered via
``_register_transition_function_with_llm``), pipecat broadcasts a
``FunctionCallsStartedFrame`` that ``FunctionCallUserMuteStrategy`` uses to
mute the user until a ``FunctionCallResultFrame`` arrives. These tests assert
that mute behavior holds end-to-end through the engine's transition flow,
so that user audio doesn't race the node switch / extraction / context update
that runs inside the transition function.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.frames.frames import LLMContextFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMAssistantAggregatorParams,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.tests.mock_transport import MockTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.turns.user_mute import (
    CallbackUserMuteStrategy,
    FunctionCallUserMuteStrategy,
    MuteUntilFirstBotCompleteUserMuteStrategy,
)

from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_variable_extractor import (
    VariableExtractionManager,
)
from api.services.workflow.workflow_graph import WorkflowGraph
from pipecat.tests import MockLLMService, MockTTSService


async def _build_engine_and_pipeline(
    workflow: WorkflowGraph,
    mock_llm: MockLLMService,
):
    """Set up engine + pipeline mirroring the non-realtime production wiring.

    Returns (engine, task, function_call_mute_strategy, user_context_aggregator).
    """
    tts = MockTTSService(mock_audio_duration_ms=40, frame_delay=0)

    transport = MockTransport(
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
        ),
    )

    context = LLMContext()

    engine = PipecatEngine(
        llm=mock_llm,
        context=context,
        workflow=workflow,
        call_context_vars={"customer_name": "Test User"},
        workflow_run_id=1,
    )

    # Hold a reference so the test can introspect the in-progress set.
    function_call_mute_strategy = FunctionCallUserMuteStrategy()

    # Match run_pipeline.py's non-realtime mute-strategy stack so the test
    # exercises the same wiring that would be active in a real call.
    user_mute_strategies = [
        MuteUntilFirstBotCompleteUserMuteStrategy(),
        function_call_mute_strategy,
        CallbackUserMuteStrategy(should_mute_callback=engine.should_mute_user),
    ]

    user_params = LLMUserAggregatorParams(user_mute_strategies=user_mute_strategies)
    assistant_params = LLMAssistantAggregatorParams()

    context_aggregator = LLMContextAggregatorPair(
        context, assistant_params=assistant_params, user_params=user_params
    )
    user_context_aggregator = context_aggregator.user()
    assistant_context_aggregator = context_aggregator.assistant()

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

    task = PipelineTask(pipeline, params=PipelineParams(), enable_rtvi=False)
    engine.set_task(task)

    return engine, task, function_call_mute_strategy, user_context_aggregator


class TestTransitionFunctionMutesUser:
    """Verify the user is muted while transition functions execute."""

    @pytest.mark.asyncio
    async def test_user_is_muted_during_transition_function(
        self, simple_workflow: WorkflowGraph
    ):
        """The user must be muted from the moment a transition function starts
        until its result is delivered.

        Scenario:
        1. LLM calls the ``end_call`` transition function (start → end edge).
        2. Wrap the registered handler so we can read mute state from inside it.
        3. VERIFY: the function-call mute strategy has the call in flight.
        4. VERIFY: the user aggregator's ``_user_is_muted`` flag is True.
        """
        step_0_chunks = MockLLMService.create_function_call_chunks(
            function_name="end_call",
            arguments={},
            tool_call_id="call_end_1",
        )
        llm = MockLLMService(mock_steps=[step_0_chunks], chunk_delay=0.001)

        (
            engine,
            task,
            function_call_mute_strategy,
            user_context_aggregator,
        ) = await _build_engine_and_pipeline(simple_workflow, llm)

        captured_states: list[dict] = []

        # Wrap register_function so we can introspect mute state from inside
        # the transition handler. We must wrap *after* the engine is created
        # but *before* set_node registers the transition functions.
        original_register_function = llm.register_function

        def wrapping_register_function(name, func, *args, **kwargs):
            async def wrapped(function_call_params):
                # Yield once so the user aggregator has a chance to drain
                # the broadcasted FunctionCallsStartedFrame and update its
                # mute state before we sample it.
                await asyncio.sleep(0.02)
                captured_states.append(
                    {
                        "name": name,
                        "function_call_in_progress": bool(
                            function_call_mute_strategy._function_call_in_progress
                        ),
                        "user_is_muted": user_context_aggregator._user_is_muted,
                        "tool_call_ids": set(
                            function_call_mute_strategy._function_call_in_progress
                        ),
                    }
                )
                return await func(function_call_params)

            return original_register_function(name, wrapped, *args, **kwargs)

        llm.register_function = wrapping_register_function

        with patch(
            "api.db:db_client.get_organization_id_by_workflow_run_id",
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
                        await engine.set_node(engine.workflow.start_node_id)
                        await engine.llm.queue_frame(LLMContextFrame(engine.context))

                    await asyncio.wait_for(
                        asyncio.gather(run_pipeline(), initialize_engine()),
                        timeout=10.0,
                    )

        assert len(captured_states) == 1, (
            f"Expected the transition function to be invoked exactly once, "
            f"got {len(captured_states)}: {captured_states}"
        )
        state = captured_states[0]
        assert state["name"] == "end_call"
        assert state["function_call_in_progress"], (
            "FunctionCallUserMuteStrategy should have the transition call in "
            f"progress while the handler runs (state={state})"
        )
        assert "call_end_1" in state["tool_call_ids"], (
            f"Expected tool_call_id 'call_end_1' to be tracked, got {state['tool_call_ids']}"
        )
        assert state["user_is_muted"], (
            "User aggregator's _user_is_muted should be True during the "
            f"transition function (state={state})"
        )

    @pytest.mark.asyncio
    async def test_user_is_unmuted_after_transition_function_returns(
        self, simple_workflow: WorkflowGraph
    ):
        """After the transition function's result is delivered, the function-call
        mute strategy should clear its in-progress set. Other strategies in the
        stack (CallbackUserMuteStrategy via engine.should_mute_user) may still
        keep the pipeline muted because end_call_with_reason fires when the
        engine reaches the End node, but the function-call strategy itself
        must release its hold.
        """
        step_0_chunks = MockLLMService.create_function_call_chunks(
            function_name="end_call",
            arguments={},
            tool_call_id="call_end_1",
        )
        llm = MockLLMService(mock_steps=[step_0_chunks], chunk_delay=0.001)

        (
            engine,
            task,
            function_call_mute_strategy,
            _user_context_aggregator,
        ) = await _build_engine_and_pipeline(simple_workflow, llm)

        with patch(
            "api.db:db_client.get_organization_id_by_workflow_run_id",
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
                        await engine.set_node(engine.workflow.start_node_id)
                        await engine.llm.queue_frame(LLMContextFrame(engine.context))

                    await asyncio.wait_for(
                        asyncio.gather(run_pipeline(), initialize_engine()),
                        timeout=10.0,
                    )

        assert function_call_mute_strategy._function_call_in_progress == set(), (
            "FunctionCallUserMuteStrategy should have cleared its in-progress "
            "set after the transition function's result was delivered, got "
            f"{function_call_mute_strategy._function_call_in_progress}"
        )
