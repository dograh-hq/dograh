from unittest.mock import AsyncMock, patch

import pytest

from api.enums import TelephonyCallStatus, WorkflowRunState
from api.services.workflow_run_failure import mark_workflow_run_failed
from api.tasks.function_names import FunctionNames


@pytest.mark.asyncio
async def test_mark_workflow_run_failed_records_error_and_completes_run():
    enqueue = AsyncMock()
    with (
        patch("api.services.workflow_run_failure.db_client") as mock_db,
        patch("api.tasks.arq.enqueue_job", enqueue),
    ):
        mock_db.update_workflow_run = AsyncMock()

        await mark_workflow_run_failed(597930, "You have exhausted your credits")

    mock_db.update_workflow_run.assert_awaited_once()
    update = mock_db.update_workflow_run.await_args.kwargs
    assert update["run_id"] == 597930
    assert update["is_completed"] is True
    assert update["state"] == WorkflowRunState.COMPLETED.value
    assert update["usage_info"] == {"call_duration_seconds": 0}
    assert update["gathered_context"] == {
        "error": "You have exhausted your credits",
        "call_disposition": TelephonyCallStatus.ERROR.value,
        "mapped_call_disposition": TelephonyCallStatus.ERROR.value,
    }

    [failure_event] = update["logs"]["realtime_feedback_events"]
    assert failure_event["type"] == "rtf-pipeline-error"
    assert failure_event["payload"] == {
        "error": "You have exhausted your credits",
        "fatal": True,
        "processor": "call_start",
    }
    assert failure_event["turn"] == 0
    assert failure_event["timestamp"]

    enqueue.assert_awaited_once_with(
        FunctionNames.RUN_INTEGRATIONS_POST_WORKFLOW_RUN,
        597930,
    )


@pytest.mark.asyncio
async def test_mark_workflow_run_failed_swallows_db_errors():
    enqueue = AsyncMock()
    with (
        patch("api.services.workflow_run_failure.db_client") as mock_db,
        patch("api.tasks.arq.enqueue_job", enqueue),
    ):
        mock_db.update_workflow_run = AsyncMock(
            side_effect=ValueError("Workflow run with ID 1 not found")
        )

        await mark_workflow_run_failed(1, "Quota exceeded")

    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_workflow_run_failed_swallows_enqueue_errors():
    enqueue = AsyncMock(side_effect=RuntimeError("Redis unavailable"))
    with (
        patch("api.services.workflow_run_failure.db_client") as mock_db,
        patch("api.tasks.arq.enqueue_job", enqueue),
    ):
        mock_db.update_workflow_run = AsyncMock()

        await mark_workflow_run_failed(2, "Failed to initiate call")

    mock_db.update_workflow_run.assert_awaited_once()
    enqueue.assert_awaited_once()
