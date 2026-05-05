"""Shared scaffolding for ``_run_pipeline`` integration tests.

Both ``test_run_pipeline.py`` and ``test_run_pipeline_text_greeting.py``
drive the real ``_run_pipeline`` end-to-end with the same set of external
boundaries patched out (STT/LLM/TTS factories, S3 recording fetcher,
PostHog publisher, ARQ enqueuer, real-time feedback observer). This
module centralises that scaffolding so each test only declares the bits
that differ — its workflow definition and any preconfigured mocks.

Provided here:

- ``USER_CONFIGURATION``: a minimal user-configuration dict with valid
  provider/model values; the keys themselves are dummy.
- ``PassthroughProcessor``: an STT stand-in that forwards frames as-is.
- ``NoopFeedbackObserver``: a ``RealtimeFeedbackObserver`` stand-in with
  no WebSocket / clock-task side effects.
- ``patch_run_pipeline_externals``: ``contextmanager`` that applies the
  full patch set and captures the constructed ``PipelineTask`` for the
  caller. Optional ``llm`` / ``tts`` arguments inject preconfigured
  mocks; otherwise blank ``MockLLMService`` / ``MockTTSService``
  instances are constructed per-call.
- ``create_workflow_run_rows``: helper that creates the org / user /
  user-configuration / workflow / workflow-run rows for an integration
  test. Each test wires this through its own thin fixture so the
  workflow definition stays local to the test.
"""

from contextlib import ExitStack, contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

from api.db.models import OrganizationModel, UserModel
from api.enums import WorkflowRunMode
from pipecat.frames.frames import Frame
from pipecat.observers.base_observer import BaseObserver
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.tests import MockLLMService, MockTTSService

USER_CONFIGURATION: dict[str, Any] = {
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


class PassthroughProcessor(FrameProcessor):
    """Stand-in for the STT processor: forwards every frame untouched."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class NoopFeedbackObserver(BaseObserver):
    """Stand-in for ``RealtimeFeedbackObserver``: no WS / no clock task."""

    def __init__(self, *_args, **_kwargs):
        super().__init__()

    async def cleanup(self):
        pass


@contextmanager
def patch_run_pipeline_externals(
    captured_task: list,
    *,
    llm: MockLLMService | None = None,
    tts: MockTTSService | None = None,
):
    """Patch the externally-talking pieces of ``_run_pipeline`` and capture
    the constructed ``PipelineTask`` so tests can drive it from outside.

    Args:
        captured_task: A list the constructed ``PipelineTask`` is appended
            to. Tests read ``captured_task[0]`` to get a handle on the task
            (to wait on its start event, queue frames, cancel it, etc.).
        llm: Optional pre-built ``MockLLMService``. When given, every call
            to ``create_llm_service`` returns this same instance (so the
            test can inspect its ``mock_steps`` / ``current_step``).
            When ``None``, a blank ``MockLLMService`` is constructed.
        tts: Optional pre-built ``MockTTSService``. Same semantics as
            ``llm``: pass an instance to share state with the test, or
            ``None`` to use a fresh one.
    """
    from api.services.pipecat import pipeline_builder as _pipeline_builder

    original_create_task = _pipeline_builder.create_pipeline_task

    def _capture_task(*args, **kwargs):
        task = original_create_task(*args, **kwargs)
        captured_task.append(task)
        return task

    def _llm_factory(*_args, **_kwargs):
        return llm if llm is not None else MockLLMService(api_key="test")

    def _tts_factory(*_args, **_kwargs):
        return tts if tts is not None else MockTTSService()

    with ExitStack() as stack:
        # Replace service factories with in-process test doubles.
        stack.enter_context(
            patch(
                "api.services.pipecat.run_pipeline.create_llm_service",
                _llm_factory,
            )
        )
        stack.enter_context(
            patch(
                "api.services.pipecat.run_pipeline.create_stt_service",
                lambda *_args, **_kwargs: PassthroughProcessor(),
            )
        )
        stack.enter_context(
            patch(
                "api.services.pipecat.run_pipeline.create_tts_service",
                _tts_factory,
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
        stack.enter_context(
            patch(
                "api.services.pipecat.event_handlers.enqueue_job",
                new=AsyncMock(),
            )
        )
        # Skip the real-time feedback observer (WebSocket / log-buffer streaming).
        stack.enter_context(
            patch(
                "api.services.pipecat.run_pipeline.RealtimeFeedbackObserver",
                NoopFeedbackObserver,
            )
        )
        # Disposition mapper would otherwise call out to the LLM.
        stack.enter_context(
            patch(
                "api.services.workflow.pipecat_engine.apply_disposition_mapping",
                new_callable=AsyncMock,
                return_value="completed",
            )
        )
        # Capture the PipelineTask so the test can drive it from outside.
        stack.enter_context(
            patch(
                "api.services.pipecat.run_pipeline.create_pipeline_task",
                side_effect=_capture_task,
            )
        )
        yield


async def create_workflow_run_rows(
    db_session,
    async_session,
    *,
    workflow_definition: dict,
    name_prefix: str,
    provider_id_suffix: str,
):
    """Create org / user / user-configuration / workflow / workflow-run rows
    in the test database for a ``_run_pipeline`` integration test.

    Args:
        db_session: The patched ``DBClient`` from the ``db_session`` fixture.
        async_session: The raw ``AsyncSession`` from the ``async_session``
            fixture (used to add the org/user rows directly).
        workflow_definition: The dict that becomes
            ``WorkflowModel.workflow_definition`` and the V1 workflow_json.
        name_prefix: Used to build human-readable workflow / run names.
        provider_id_suffix: Used to generate unique ``provider_id`` values
            for the org and user rows so concurrent or repeated test runs
            don't collide.

    Returns:
        Tuple of (workflow_run, user, workflow).
    """
    from api.schemas.user_configuration import UserConfiguration

    org = OrganizationModel(provider_id=f"test-org-{provider_id_suffix}")
    async_session.add(org)
    await async_session.flush()

    user = UserModel(
        provider_id=f"test-user-{provider_id_suffix}",
        selected_organization_id=org.id,
    )
    async_session.add(user)
    await async_session.flush()

    await db_session.update_user_configuration(
        user_id=user.id,
        configuration=UserConfiguration.model_validate(USER_CONFIGURATION),
    )

    workflow = await db_session.create_workflow(
        name=f"{name_prefix} Workflow",
        workflow_definition=workflow_definition,
        user_id=user.id,
        organization_id=org.id,
    )

    workflow_run = await db_session.create_workflow_run(
        name=f"{name_prefix} Run",
        workflow_id=workflow.id,
        mode=WorkflowRunMode.SMALLWEBRTC.value,
        user_id=user.id,
    )

    return workflow_run, user, workflow


# Keep the module's public surface explicit so ``import *`` doesn't grab
# transitive imports.
__all__ = [
    "USER_CONFIGURATION",
    "PassthroughProcessor",
    "NoopFeedbackObserver",
    "patch_run_pipeline_externals",
    "create_workflow_run_rows",
]
