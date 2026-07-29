"""Record pre-call failures on the workflow run so they surface in the UI.

Workflow runs are created before quota authorization and call initiation.
When either step fails, the rejection travels back to the telephony provider
or API caller, but nothing reaches the run's owner: the run sits in
``initialized`` state forever with an empty detail page. Writing the error
into ``gathered_context`` makes it visible on the run detail page and marks
the run completed, matching the campaign dispatcher's convention.
"""

from loguru import logger

from api.db import db_client
from api.enums import WorkflowRunState


async def mark_workflow_run_failed(workflow_run_id: int, error_message: str) -> None:
    """Complete the run with a user-visible error.

    Best-effort: callers invoke this while rejecting a call, so a bookkeeping
    failure must never mask the original rejection path.
    """
    try:
        await db_client.update_workflow_run(
            run_id=workflow_run_id,
            is_completed=True,
            state=WorkflowRunState.COMPLETED.value,
            gathered_context={"error": error_message},
        )
    except Exception as e:
        logger.error(
            f"Failed to record failure on workflow run {workflow_run_id}: {e}"
        )
