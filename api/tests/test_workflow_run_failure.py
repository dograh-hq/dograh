from unittest.mock import AsyncMock, patch

import pytest

from api.enums import WorkflowRunState
from api.services.workflow_run_failure import mark_workflow_run_failed


@pytest.mark.asyncio
async def test_mark_workflow_run_failed_records_error_and_completes_run():
    with patch("api.services.workflow_run_failure.db_client") as mock_db:
        mock_db.update_workflow_run = AsyncMock()

        await mark_workflow_run_failed(597930, "You have exhausted your credits")

    mock_db.update_workflow_run.assert_awaited_once_with(
        run_id=597930,
        is_completed=True,
        state=WorkflowRunState.COMPLETED.value,
        gathered_context={"error": "You have exhausted your credits"},
    )


@pytest.mark.asyncio
async def test_mark_workflow_run_failed_swallows_db_errors():
    with patch("api.services.workflow_run_failure.db_client") as mock_db:
        mock_db.update_workflow_run = AsyncMock(
            side_effect=ValueError("Workflow run with ID 1 not found")
        )

        await mark_workflow_run_failed(1, "Quota exceeded")
