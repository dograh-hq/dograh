"""Finalize workflow runs that fail before the call pipeline starts.

Workflow runs are created before quota authorization and call initiation.
When either step fails, the rejection travels back to the telephony provider
or API caller, but nothing reaches the run's owner: the run sits in
``initialized`` state forever with an empty detail page. Writing the error
into ``gathered_context`` makes it available to integrations. A persisted
real-time feedback event makes the failure visible in the run transcript, and
the post-run integration job delivers configured webhooks.
"""

from datetime import UTC, datetime

from loguru import logger

from api.db import db_client
from api.enums import TelephonyCallStatus, WorkflowRunState
from api.services.pipecat.realtime_feedback_events import (
    build_pipeline_error_event,
    stamp_realtime_feedback_event,
)
from api.tasks.function_names import FunctionNames


async def mark_workflow_run_failed(workflow_run_id: int, error_message: str) -> None:
    """Complete the run with a user-visible error and notify integrations.

    Best-effort: callers invoke this while rejecting a call, so a bookkeeping
    failure must never mask the original rejection path.
    """
    error_disposition = TelephonyCallStatus.ERROR.value
    failure_event = stamp_realtime_feedback_event(
        build_pipeline_error_event(
            error=error_message,
            fatal=True,
            processor="call_start",
        ),
        timestamp=datetime.now(UTC).isoformat(timespec="milliseconds"),
        turn=0,
    )

    try:
        await db_client.update_workflow_run(
            run_id=workflow_run_id,
            is_completed=True,
            state=WorkflowRunState.COMPLETED.value,
            usage_info={"call_duration_seconds": 0},
            gathered_context={
                "error": error_message,
                "call_disposition": error_disposition,
                "mapped_call_disposition": error_disposition,
            },
            logs={"realtime_feedback_events": [failure_event]},
        )
    except Exception as e:  # noqa: BLE001 - bookkeeping must remain best-effort
        logger.error(f"Failed to record failure on workflow run {workflow_run_id}: {e}")
        return

    try:
        # Imported lazily to avoid loading the ARQ worker/task graph when route
        # modules import this helper.
        from api.tasks.arq import enqueue_job

        await enqueue_job(
            FunctionNames.RUN_INTEGRATIONS_POST_WORKFLOW_RUN,
            workflow_run_id,
        )
    except Exception as e:  # noqa: BLE001 - notification must not mask rejection
        logger.error(
            f"Failed to enqueue integrations for failed workflow run "
            f"{workflow_run_id}: {e}"
        )
