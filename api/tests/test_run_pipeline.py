"""Integration tests for ``api.services.pipecat.run_pipeline._run_pipeline``.

Drives the actual ``_run_pipeline`` against the test database with real
DB rows (organization, user, user configuration, workflow, workflow run)
and pipecat's real ``MockTransport`` / ``Pipeline`` / ``PipelineTask``.
The only patches are for things that talk to genuinely external systems:

- The STT/LLM/TTS service factories — replaced by lightweight test
  doubles so we don't hit OpenAI/Deepgram/Cartesia.
- ``create_recording_audio_fetcher`` — replaced because it talks to S3.
- ``_capture_call_event`` — fire-and-forget PostHog publisher.
- ``enqueue_job`` — there is no ARQ worker in the test environment.

Verifies that the wiring done by ``_run_pipeline`` (in particular
``register_event_handlers``) produces the right behaviour end-to-end:
``maybe_trigger_initial_response`` fires (``engine.set_node`` runs), and
on shutdown the workflow run is persisted with the expected state,
completion flag, and ``gathered_context`` entries.
"""

import asyncio
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.frames.frames import Frame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.tests.mock_transport import MockTransport
from pipecat.transports.base_transport import TransportParams

from api.db.models import OrganizationModel, UserModel
from api.enums import WorkflowRunMode, WorkflowRunState
from api.services.pipecat.audio_config import create_audio_config
from api.services.pipecat.run_pipeline import _run_pipeline
from pipecat.tests import MockLLMService, MockTTSService

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

USER_CONFIGURATION = {
    "is_realtime": False,
    "stt": {
        "provider": "deepgram",
        "model": "nova-3",
        "api_key": "test-key",
    },
    "tts": {
        "provider": "cartesia",
        "model": "sonic-2",
        "api_key": "test-key",
        "voice_id": "test-voice",
    },
    "llm": {
        "provider": "openai",
        "model": "gpt-4.1",
        "api_key": "test-key",
    },
}


class _PassthroughProcessor(FrameProcessor):
    """Stand-in for the STT processor: forwards every frame untouched."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


@pytest.fixture
async def workflow_run_setup(db_session, async_session):
    """Create an org, user, user_configuration, workflow, and workflow_run
    in the test database. Returns (workflow_run, user, workflow)."""
    from api.schemas.user_configuration import UserConfiguration

    org = OrganizationModel(provider_id="test-org-event-handlers")
    async_session.add(org)
    await async_session.flush()

    user = UserModel(
        provider_id="test-user-event-handlers",
        selected_organization_id=org.id,
    )
    async_session.add(user)
    await async_session.flush()

    await db_session.update_user_configuration(
        user_id=user.id,
        configuration=UserConfiguration.model_validate(USER_CONFIGURATION),
    )

    workflow = await db_session.create_workflow(
        name="Event Handler Integration Workflow",
        workflow_definition=WORKFLOW_DEFINITION,
        user_id=user.id,
        organization_id=org.id,
    )

    workflow_run = await db_session.create_workflow_run(
        name="Integration Run",
        workflow_id=workflow.id,
        mode=WorkflowRunMode.SMALLWEBRTC.value,
        user_id=user.id,
    )

    return workflow_run, user, workflow


@contextmanager
def _patch_externals(captured_task: list):
    """Patch the externally-talking pieces and capture the PipelineTask
    instance so the test can cancel the run from outside."""
    from api.services.pipecat import pipeline_builder as _pipeline_builder

    original_create_task = _pipeline_builder.create_pipeline_task

    def _capture_task(*args, **kwargs):
        task = original_create_task(*args, **kwargs)
        captured_task.append(task)
        return task

    with ExitStack() as stack:
        # Replace service factories with in-process test doubles.
        stack.enter_context(
            patch(
                "api.services.pipecat.run_pipeline.create_llm_service",
                lambda *_args, **_kwargs: MockLLMService(api_key="test"),
            )
        )
        stack.enter_context(
            patch(
                "api.services.pipecat.run_pipeline.create_stt_service",
                lambda *_args, **_kwargs: _PassthroughProcessor(),
            )
        )
        stack.enter_context(
            patch(
                "api.services.pipecat.run_pipeline.create_tts_service",
                lambda *_args, **_kwargs: MockTTSService(),
            )
        )
        # S3 — the recording fetcher would otherwise resolve org-scoped recordings.
        stack.enter_context(
            patch(
                "api.services.pipecat.run_pipeline.create_recording_audio_fetcher",
                lambda *_args, **_kwargs: AsyncMock(return_value=None),
            )
        )
        # External fire-and-forget integrations.
        stack.enter_context(
            patch(
                "api.services.pipecat.event_handlers._capture_call_event",
                new=AsyncMock(),
            )
        )
        # Mock enqueue jobs to ARQ
        stack.enter_context(
            patch(
                "api.services.pipecat.event_handlers.enqueue_job",
                new=AsyncMock(),
            )
        )
        # Capture the PipelineTask so we can cancel the run from outside.
        stack.enter_context(
            patch(
                "api.services.pipecat.run_pipeline.create_pipeline_task",
                side_effect=_capture_task,
            )
        )
        yield


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
    with _patch_externals(captured_task):
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
