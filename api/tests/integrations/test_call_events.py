"""Exercise enabled event capture through the real call completion path."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pipecat.tests.mock_transport import MockTransport
from pipecat.transports.base_transport import TransportParams

from api.enums import OrganizationConfigurationKey, WorkflowRunMode, WorkflowRunState
from api.services.observability.call_events.runtime import CallEventsSession
from api.services.pipecat.audio_config import create_audio_config
from api.services.pipecat.realtime_feedback_observer import RealtimeFeedbackObserver
from api.services.pipecat.run_pipeline import _run_pipeline
from api.services.pipecat.worker_runner import wait_for_pipeline_worker_started
from api.services.workflow.pipecat_engine import PipecatEngine
from api.tests.integrations._run_pipeline_helpers import (
    create_workflow_run_rows,
    patch_run_pipeline_externals,
)
from api.tests.integrations.test_run_pipeline import WORKFLOW_DEFINITION


@pytest.mark.parametrize(
    "finalization_error", [None, RuntimeError, asyncio.CancelledError]
)
async def test_completed_call_exports_events_without_persisting_them(
    db_session, async_session, monkeypatch, finalization_error
):
    workflow_run, user, workflow = await create_workflow_run_rows(
        db_session,
        async_session,
        workflow_definition=WORKFLOW_DEFINITION,
        name_prefix="Call Events",
        provider_id_suffix="call-events",
    )
    monkeypatch.setattr("api.constants.AUTH_PROVIDER", "local")
    await db_session.upsert_configuration(
        workflow.organization_id,
        OrganizationConfigurationKey.CALL_EVENTS.value,
        {
            "enabled": True,
            "sink_type": "bigquery",
            "config": {
                "table": "example-project.analytics.call_events",
                "auth_mode": "application_default",
            },
        },
    )
    submit = Mock()
    monkeypatch.setattr(
        "api.services.observability.call_events.delivery.submit", submit
    )
    finish_calls = Mock()
    if finalization_error is not None:

        async def fail_finish(session, gathered_context=None):
            # Keep the real recorder's watchdogs tidy while simulating an error
            # escaping both normal finalization and the pipeline's fallback.
            await session.recorder.cleanup()
            finish_calls()
            raise finalization_error("diagnostic finalization failed")

        monkeypatch.setattr(CallEventsSession, "finish", fail_finish)

    cleanup_steps = []
    original_close_mcp = PipecatEngine.close_mcp_sessions
    original_cleanup_feedback = RealtimeFeedbackObserver.cleanup

    async def close_mcp(engine):
        await original_close_mcp(engine)
        cleanup_steps.append("mcp")

    async def cleanup_feedback(observer):
        await original_cleanup_feedback(observer)
        cleanup_steps.append("feedback")

    monkeypatch.setattr(PipecatEngine, "close_mcp_sessions", close_mcp)
    monkeypatch.setattr(RealtimeFeedbackObserver, "cleanup", cleanup_feedback)
    notify_completed = AsyncMock()
    monkeypatch.setattr(
        "api.services.pipecat.event_handlers.notify_campaign_call_completed",
        notify_completed,
    )
    transport = MockTransport(
        TransportParams(
            audio_in_enabled=True, audio_out_enabled=True, audio_out_end_silence_secs=0
        )
    )
    captured = []
    with (
        patch_run_pipeline_externals(captured),
        patch(
            "api.services.pipecat.run_pipeline.RealtimeFeedbackObserver",
            RealtimeFeedbackObserver,
        ),
    ):
        run = asyncio.create_task(
            _run_pipeline(
                transport=transport,
                workflow_id=workflow.id,
                workflow_run_id=workflow_run.id,
                user_id=user.id,
                audio_config=create_audio_config(WorkflowRunMode.SMALLWEBRTC.value),
                user_provider_id=user.provider_id,
            )
        )
        try:
            for _ in range(60):
                if captured or run.done():
                    break
                await asyncio.sleep(0.05)
            if run.done():
                run.result()
            assert captured
            task = captured[0]
            await wait_for_pipeline_worker_started(task, timeout=3, run_task=run)
            await asyncio.sleep(0.1)
            await task.cancel()
            await asyncio.wait_for(run, timeout=8)
        finally:
            if not run.done():
                run.cancel()
                await asyncio.gather(run, return_exceptions=True)
    # Workers also clean their shared observer; the final two calls must be
    # the pipeline fallback closing MCP and then cleaning its observer.
    assert cleanup_steps[-2:] == ["mcp", "feedback"]
    notify_completed.assert_awaited_once_with(None, workflow_run.id)
    if finalization_error is not None:
        assert finish_calls.call_count == 2
        submit.assert_not_called()
    else:
        submit.assert_called_once()
        org, _, events, _ = submit.call_args.args
        assert org == workflow.organization_id
        assert sum(e.event == "call_ended" for e in events) == 1
        assert events[-1].event == "call_ended"
        assert all(e.run_id == workflow_run.id and e.org_id == org for e in events)
    refreshed = await db_session.get_workflow_run_by_id(workflow_run.id)
    assert refreshed.is_completed
    assert refreshed.state == WorkflowRunState.COMPLETED.value
    assert "call_events" not in (refreshed.logs or {})
    assert all(
        "event" not in e
        for e in (refreshed.logs or {}).get("realtime_feedback_events", [])
    )
