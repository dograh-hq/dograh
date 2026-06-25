from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.enums import TelephonyCallStatus, WorkflowRunState
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)
from api.tasks.function_names import FunctionNames


@pytest.mark.asyncio
async def test_initialized_no_answer_enqueues_workflow_completion():
    workflow_run = SimpleNamespace(
        id=123,
        campaign_id=None,
        queued_run_id=None,
        state=WorkflowRunState.INITIALIZED.value,
        is_completed=False,
        logs={"telephony_status_callbacks": []},
        gathered_context={"call_tags": ["existing"]},
    )
    status = StatusCallbackRequest(
        call_id="call-123",
        status="No-Answer",
    )

    with (
        patch("api.services.telephony.status_processor.db_client") as mock_db,
        patch(
            "api.services.telephony.status_processor.enqueue_job",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        mock_db.get_workflow_run_by_id = AsyncMock(return_value=workflow_run)
        mock_db.update_workflow_run = AsyncMock()

        await _process_status_update(123, status)

    log_update = mock_db.update_workflow_run.await_args_list[0].kwargs
    callback_log = log_update["logs"]["telephony_status_callbacks"][0]
    assert callback_log["status"] == "no-answer"
    assert callback_log["call_id"] == "call-123"

    completion_update = mock_db.update_workflow_run.await_args_list[1].kwargs
    assert completion_update["run_id"] == 123
    assert completion_update["is_completed"] is True
    assert completion_update["state"] == WorkflowRunState.COMPLETED.value
    assert completion_update["usage_info"] == {"call_duration_seconds": 0}
    assert completion_update["gathered_context"] == {
        "call_tags": ["existing", "not_connected", "telephony_no-answer"],
        "call_disposition": "no-answer",
        "mapped_call_disposition": "no-answer",
        "call_id": "call-123",
    }
    mock_enqueue.assert_awaited_once_with(
        FunctionNames.RUN_INTEGRATIONS_POST_WORKFLOW_RUN, 123
    )


@pytest.mark.asyncio
async def test_running_terminal_status_does_not_enqueue_workflow_completion():
    workflow_run = SimpleNamespace(
        id=456,
        campaign_id=None,
        queued_run_id=None,
        state=WorkflowRunState.RUNNING.value,
        is_completed=False,
        logs={"telephony_status_callbacks": []},
        gathered_context={"call_tags": ["not_connected"]},
    )
    status = StatusCallbackRequest(
        call_id="call-456",
        status=TelephonyCallStatus.FAILED,
        duration="7",
    )

    with (
        patch("api.services.telephony.status_processor.db_client") as mock_db,
        patch(
            "api.services.telephony.status_processor.enqueue_job",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        mock_db.get_workflow_run_by_id = AsyncMock(return_value=workflow_run)
        mock_db.update_workflow_run = AsyncMock()

        await _process_status_update(456, status)

    completion_update = mock_db.update_workflow_run.await_args_list[1].kwargs
    assert "usage_info" not in completion_update
    assert completion_update["gathered_context"]["call_tags"] == [
        "not_connected",
        "telephony_failed",
    ]
    mock_enqueue.assert_not_awaited()
