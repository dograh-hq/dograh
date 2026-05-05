"""Integration tests for ``api.services.pipecat.run_pipeline._run_pipeline``.

Drives the actual ``_run_pipeline`` against the test database with real
DB rows (organization, user, user configuration, workflow, workflow run)
and pipecat's real ``MockTransport`` / ``Pipeline`` / ``PipelineTask``.
The only patches are for things that talk to genuinely external systems;
those are applied via ``patch_run_pipeline_externals`` from the shared
helpers module.

Verifies that the wiring done by ``_run_pipeline`` (in particular
``register_event_handlers``) produces the right behaviour end-to-end:
``maybe_trigger_initial_response`` fires (``engine.set_node`` runs), and
on shutdown the workflow run is persisted with the expected state,
completion flag, and ``gathered_context`` entries.
"""

import asyncio

import pytest

from api.enums import WorkflowRunMode, WorkflowRunState
from api.services.pipecat.audio_config import create_audio_config
from api.services.pipecat.run_pipeline import _run_pipeline
from api.tests.integrations._run_pipeline_helpers import (
    create_workflow_run_rows,
    patch_run_pipeline_externals,
)
from pipecat.tests.mock_transport import MockTransport
from pipecat.transports.base_transport import TransportParams

WORKFLOW_DEFINITION = {
    "nodes": [
        {
            "id": "start",
            "type": "startCall",
            "position": {"x": 0, "y": 0},
            "data": {
                "name": "Start",
                "prompt": "You are a helpful assistant. Greet the user briefly.",
                "is_start": True,
                "allow_interrupt": False,
                "add_global_prompt": False,
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
            "data": {"label": "End", "condition": "When the user wants to end."},
        }
    ],
}


@pytest.fixture
async def workflow_run_setup(db_session, async_session):
    """Create org/user/user_configuration/workflow/workflow_run rows in the
    test database. Returns (workflow_run, user, workflow)."""
    return await create_workflow_run_rows(
        db_session,
        async_session,
        workflow_definition=WORKFLOW_DEFINITION,
        name_prefix="Event Handler Integration",
        provider_id_suffix="event-handlers",
    )


@pytest.mark.asyncio
async def test_run_pipeline_fires_initial_response_and_completes_run(
    workflow_run_setup, db_session
):
    """End-to-end: _run_pipeline boots, register_event_handlers wires up,
    on_pipeline_started + on_client_connected both fire, the initial
    response is triggered (set_node), and on_pipeline_finished updates
    the workflow_run row to COMPLETED."""
    workflow_run, user, workflow = workflow_run_setup
    transport = MockTransport(
        TransportParams(audio_in_enabled=True, audio_out_enabled=True)
    )

    captured_task: list = []
    audio_config = create_audio_config(WorkflowRunMode.SMALLWEBRTC.value)
    with patch_run_pipeline_externals(captured_task):
        run_coro = _run_pipeline(
            transport=transport,
            workflow_id=workflow.id,
            workflow_run_id=workflow_run.id,
            user_id=user.id,
            audio_config=audio_config,
            user_provider_id=user.provider_id,
        )
        run_task = asyncio.create_task(run_coro)

        # Wait until create_pipeline_task is invoked. Surface any
        # exception from _run_pipeline immediately rather than swallowing
        # it during the wait loop.
        for _ in range(60):
            if captured_task or run_task.done():
                break
            await asyncio.sleep(0.05)
        if run_task.done() and not captured_task:
            run_task.result()  # re-raise the failure
        assert captured_task, "create_pipeline_task was never invoked"
        pipeline_task = captured_task[0]
        await asyncio.wait_for(pipeline_task._pipeline_start_event.wait(), timeout=3.0)
        # Let the initial response handler (set_node, queue LLMContextFrame)
        # complete before tearing things down.
        await asyncio.sleep(0.1)
        await pipeline_task.cancel()
        await asyncio.wait_for(run_task, timeout=5.0)

    # Verify the run was completed end-to-end via the real on_pipeline_finished
    # handler — DB side effects, not mock assertions.
    refreshed = await db_session.get_workflow_run_by_id(workflow_run.id)
    assert refreshed.is_completed is True
    assert refreshed.state == WorkflowRunState.COMPLETED.value
    # set_node("start") populates "nodes_visited" via _gathered_context, and
    # on_pipeline_finished merges call_tags into gathered_context.
    assert "Start" in refreshed.gathered_context.get("nodes_visited", [])
    assert "call_tags" in refreshed.gathered_context
