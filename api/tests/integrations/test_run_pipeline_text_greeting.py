"""Integration test for the text-greeting flow through ``_run_pipeline``.

Drives the full pipeline produced by ``_run_pipeline`` against the test
database with a workflow whose start node has a text greeting configured.
The flow under test:

1. ``maybe_trigger_initial_response`` (in ``event_handlers.py``) sees a
   text greeting and queues ``TTSSpeakFrame(greeting)``.
2. ``MockTTSService`` synthesises audio for the greeting; the real
   ``MediaSender`` machinery in ``MockOutputTransport`` emits
   ``BotStartedSpeakingFrame`` and ``BotStoppedSpeakingFrame``.
3. The TTS service emits an ``LLMAssistantPushAggregationFrame`` after
   ``TTSStoppedFrame``, so the greeting is appended to the assistant
   context by ``LLMAssistantAggregator``.
4. We then push a ``TranscriptionFrame`` into the pipeline. After the
   user-turn-stop timeout, ``LLMUserAggregator`` pushes a context frame
   to the LLM, ``MockLLMService`` returns an ``end_call`` tool call, and
   the engine's transition function moves to the end node and calls
   ``end_call_with_reason``.
5. ``on_pipeline_finished`` records the run as COMPLETED.

External boundaries are patched via ``patch_run_pipeline_externals``
from the shared helpers module. Preconfigured ``MockLLMService`` /
``MockTTSService`` instances are passed in so the end_call response is
deterministic and the synthesised audio length is short.
"""

import asyncio

import pytest
from pipecat.frames.frames import TranscriptionFrame
from pipecat.tests.mock_transport import MockTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.utils.time import time_now_iso8601

from api.enums import WorkflowRunMode, WorkflowRunState
from api.services.pipecat.audio_config import create_audio_config
from api.services.pipecat.run_pipeline import _run_pipeline
from api.tests.integrations._run_pipeline_helpers import (
    create_workflow_run_rows,
    patch_run_pipeline_externals,
)
from pipecat.tests import MockLLMService, MockTTSService

GREETING_TEXT = (
    "Thanks for calling Happy Feet, this is Sarah. How can I help you today?"
)

WORKFLOW_DEFINITION = {
    "nodes": [
        {
            "id": "start",
            "type": "startCall",
            "position": {"x": 0, "y": 0},
            "data": {
                "name": "Start",
                "prompt": "You are Sarah. Help the caller and end the call when they ask.",
                "is_start": True,
                "allow_interrupt": False,
                "add_global_prompt": False,
                "greeting": GREETING_TEXT,
                "greeting_type": "text",
            },
        },
        {
            "id": "end",
            "type": "endCall",
            "position": {"x": 0, "y": 200},
            "data": {
                "name": "End",
                "prompt": "End the call politely.",
                "is_end": True,
                "allow_interrupt": False,
                "add_global_prompt": False,
            },
        },
    ],
    "edges": [
        {
            "id": "start-end",
            "source": "start",
            "target": "end",
            "data": {"label": "End Call", "condition": "When the user wants to end."},
        }
    ],
}

# Hard cap on the entire test. Without this, a hung pipeline would keep the
# pytest worker alive indefinitely (the harness has no pytest-timeout plugin).
TEST_HARD_TIMEOUT_SECONDS = 25.0


@pytest.fixture
async def workflow_run_setup(db_session, async_session):
    """Create org/user/user_configuration/workflow/workflow_run rows. The
    workflow's start node is configured with a text greeting."""
    return await create_workflow_run_rows(
        db_session,
        async_session,
        workflow_definition=WORKFLOW_DEFINITION,
        name_prefix="Text Greeting Integration",
        provider_id_suffix="text-greeting",
    )


def _greeting_in_assistant_context(context) -> bool:
    """Return True if the greeting text has been appended to the assistant context."""
    for message in context.get_messages():
        if isinstance(message, dict) and message.get("role") == "assistant":
            content = message.get("content") or ""
            if GREETING_TEXT in content:
                return True
    return False


def _find_processor_by_class_name(pipeline_task, class_name: str):
    """Walk every processor reachable from the task's pipeline (including nested
    sub-pipelines) and return the first one whose class name matches."""
    visited: set[int] = set()
    stack = [pipeline_task._pipeline]
    while stack:
        processor = stack.pop()
        if id(processor) in visited:
            continue
        visited.add(id(processor))
        if processor.__class__.__name__ == class_name:
            return processor
        sub = getattr(processor, "_processors", None)
        if sub:
            stack.extend(sub)
    return None


async def _wait_for(predicate, *, timeout: float, interval: float = 0.05) -> bool:
    """Poll ``predicate`` (sync callable returning bool) until it returns True
    or the timeout elapses. Returns the final predicate value."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return predicate()


async def _run_test_body(workflow_run_setup, db_session) -> None:
    workflow_run, user, workflow = workflow_run_setup

    # Prepare the LLM with one step: the end_call function call.
    # Edge label "End Call" maps to function name "end_call".
    end_call_chunks = MockLLMService.create_function_call_chunks(
        function_name="end_call",
        arguments={},
        tool_call_id="call_end_1",
    )
    llm = MockLLMService(mock_steps=[end_call_chunks], chunk_delay=0.001)

    # Short audio greeting so the bot finishes speaking quickly in tests.
    tts = MockTTSService(mock_audio_duration_ms=50, frame_delay=0)

    transport = MockTransport(
        TransportParams(audio_in_enabled=True, audio_out_enabled=True)
    )

    captured_task: list = []
    audio_config = create_audio_config(WorkflowRunMode.SMALLWEBRTC.value)
    pipeline_task = None

    try:
        with patch_run_pipeline_externals(captured_task, llm=llm, tts=tts):
            run_coro = _run_pipeline(
                transport=transport,
                workflow_id=workflow.id,
                workflow_run_id=workflow_run.id,
                user_id=user.id,
                audio_config=audio_config,
                user_provider_id=user.provider_id,
            )
            run_task = asyncio.create_task(run_coro)

            for _ in range(60):
                if captured_task or run_task.done():
                    break
                await asyncio.sleep(0.05)
            if run_task.done() and not captured_task:
                run_task.result()
            assert captured_task, "create_pipeline_task was never invoked"
            pipeline_task = captured_task[0]

            await asyncio.wait_for(
                pipeline_task._pipeline_start_event.wait(), timeout=3.0
            )

            # Locate the assistant aggregator's LLM context (downstream of TTS).
            # The PipelineTask wraps the user's pipeline inside another Pipeline,
            # so we walk the tree recursively.
            assistant_aggregator = _find_processor_by_class_name(
                pipeline_task, "LLMAssistantAggregator"
            )
            assert assistant_aggregator is not None, (
                "LLMAssistantAggregator not found in pipeline"
            )
            context = assistant_aggregator.context

            # Wait for the greeting to be appended to the assistant context. The
            # TTSSpeakFrame -> audio frames -> BotStoppedSpeaking -> assistant
            # aggregation push chain runs through the real pipeline.
            appeared = await _wait_for(
                lambda: _greeting_in_assistant_context(context), timeout=5.0
            )
            assert appeared, (
                "Greeting was not appended to the assistant context. "
                f"Messages: {context.get_messages()}"
            )

            # The LLM must not have been invoked yet — the greeting bypasses
            # the LLM entirely (goes straight to TTS via TTSSpeakFrame).
            assert llm.get_current_step() == 0, (
                f"LLM should not have run yet; current_step={llm.get_current_step()}"
            )

            # Now simulate the user replying. SpeechTimeoutUserTurnStopStrategy
            # (default 0.6s) ends the user turn, which triggers an LLM run;
            # the LLM returns end_call; the transition function moves to the
            # end node and ends the call.
            await pipeline_task.queue_frame(
                TranscriptionFrame(
                    text="I want to end the call now please.",
                    user_id="test-user",
                    timestamp=time_now_iso8601(),
                )
            )

            # Wait for the run to complete.
            await asyncio.wait_for(run_task, timeout=10.0)

        # Outside the patch ctx so the assertions exercise real DB state.
        # The first LLM run produces the end_call; the engine then transitions
        # to the End node and triggers a second generation (which is empty —
        # mock_steps[1] is unset). What matters is that at least one run
        # happened, i.e. the user transcript actually drove the LLM.
        assert llm.get_current_step() >= 1, (
            f"Expected at least one LLM generation; got step={llm.get_current_step()}"
        )

        refreshed = await db_session.get_workflow_run_by_id(workflow_run.id)
        assert refreshed.is_completed is True
        assert refreshed.state == WorkflowRunState.COMPLETED.value
        nodes_visited = refreshed.gathered_context.get("nodes_visited", [])
        assert "Start" in nodes_visited
        assert "End" in nodes_visited
    finally:
        # Best-effort cleanup so a partially-run pipeline doesn't leak tasks
        # past the test boundary.
        if pipeline_task is not None and not pipeline_task.has_finished():
            try:
                await asyncio.wait_for(pipeline_task.cancel(), timeout=3.0)
            except Exception:
                pass


@pytest.mark.asyncio
async def test_text_greeting_speaks_then_user_transcript_triggers_end_call(
    workflow_run_setup, db_session
):
    """End-to-end:

    - ``maybe_trigger_initial_response`` queues ``TTSSpeakFrame`` for the
      start-node text greeting.
    - ``MockTTSService`` synthesises audio; ``MockOutputTransport`` emits
      bot speaking events; the assistant aggregator appends the greeting
      to the context after the TTS turn ends.
    - We push a ``TranscriptionFrame`` into the pipeline. After the user
      turn stop timeout, ``MockLLMService`` returns an ``end_call`` tool
      call which transitions to the end node and ends the run.

    The whole body is bounded by ``TEST_HARD_TIMEOUT_SECONDS`` so a hung
    pipeline fails the test rather than wedging the test runner.
    """
    try:
        await asyncio.wait_for(
            _run_test_body(workflow_run_setup, db_session),
            timeout=TEST_HARD_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as e:
        raise AssertionError(
            f"Test exceeded hard timeout of {TEST_HARD_TIMEOUT_SECONDS}s — "
            "pipeline likely hung. Check earlier debug logs for the last frame "
            "to reach the pipeline."
        ) from e
