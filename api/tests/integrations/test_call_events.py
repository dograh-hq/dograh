"""Exercise enabled event capture through the real call completion path."""

import asyncio
from unittest.mock import Mock, patch

from pipecat.tests.mock_transport import MockTransport
from pipecat.transports.base_transport import TransportParams

from api.enums import OrganizationConfigurationKey, WorkflowRunMode
from api.services.pipecat.audio_config import create_audio_config
from api.services.pipecat.realtime_feedback_observer import RealtimeFeedbackObserver
from api.services.pipecat.run_pipeline import _run_pipeline
from api.services.pipecat.worker_runner import wait_for_pipeline_worker_started
from api.tests.integrations._run_pipeline_helpers import (
    create_workflow_run_rows,
    patch_run_pipeline_externals,
)
from api.tests.integrations.test_run_pipeline import WORKFLOW_DEFINITION


async def test_completed_call_exports_events_without_persisting_them(
    db_session, async_session, monkeypatch
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
                "table": "milo-506211.dograh.pipeline_diagnostics",
                "auth_mode": "application_default",
            },
        },
    )
    submit = Mock()
    monkeypatch.setattr(
        "api.services.observability.call_events.delivery.submit", submit
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
    submit.assert_called_once()
    org, _, events, _ = submit.call_args.args
    assert org == workflow.organization_id
    assert sum(e.event == "call_ended" for e in events) == 1
    assert events[-1].event == "call_ended"
    assert all(e.run_id == workflow_run.id and e.org_id == org for e in events)
    refreshed = await db_session.get_workflow_run_by_id(workflow_run.id)
    assert refreshed.is_completed
    assert "call_events" not in (refreshed.logs or {})
    assert all(
        "event" not in e
        for e in (refreshed.logs or {}).get("realtime_feedback_events", [])
    )
