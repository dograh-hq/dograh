"""Run extraction and completion integrations after durable call finalization."""

import time
import uuid

from loguru import logger
from pipecat.utils.run_context import set_current_org_id, set_current_run_id

from api.db import db_client
from api.services.telephony.external_pbx_writeback import sync_external_pbx_call_record
from api.services.workflow.final_extraction import run_final_extraction
from api.services.workflow_run_billing import (
    report_completed_workflow_run_platform_usage,
)
from api.tasks.run_integrations import run_integrations_post_workflow_run


async def process_workflow_completion(_ctx, workflow_run_id: int):
    """Extract, run integrations, bill and write back after artifact upload.

    New voice runs carry a durable completion record. Legacy runs and text
    chats use the same steps without participating in voice-run recovery.
    """
    set_current_run_id(str(workflow_run_id))
    run, organization_id = await db_client.get_workflow_run_with_context(
        workflow_run_id
    )
    if run is None or organization_id is None:
        return
    set_current_org_id(organization_id)
    tracked = bool((run.extra or {}).get("workflow_completion"))
    token = uuid.uuid4().hex
    steps = []
    if tracked:
        claim = await db_client.claim_workflow_completion(
            workflow_run_id, organization_id, token
        )
        if claim is None:
            return
        steps = claim.get("steps") or []
        # A failed extraction is recorded and downstream processing continues;
        # a failed persistence raises so no consumer sees an uncommitted result.
        await run_final_extraction(workflow_run_id, organization_id, token)

    run, _ = await db_client.get_workflow_run_with_context(workflow_run_id)
    disposition = (run.gathered_context or {}).get("mapped_call_disposition")
    operations = []
    if disposition:
        operations.append(
            (
                "disposition",
                lambda: db_client.add_call_disposition_code(
                    run.workflow_id, disposition
                ),
            )
        )
    operations.extend(
        [
            (
                "integrations",
                lambda: run_integrations_post_workflow_run(_ctx, workflow_run_id),
            ),
            (
                "billing",
                lambda: report_completed_workflow_run_platform_usage(workflow_run_id),
            ),
            ("pbx", lambda: sync_external_pbx_call_record(workflow_run_id)),
        ]
    )
    failed_steps = []
    for step, operation in operations:
        if step in steps:
            continue
        try:
            await operation()
        except Exception as exc:  # noqa: BLE001 - independent completion steps
            logger.error(
                "Error in completion step {} for run {}: {}", step, workflow_run_id, exc
            )
            failed_steps.append(step)
            continue
        if tracked and not await db_client.checkpoint_workflow_completion(
            workflow_run_id,
            organization_id,
            token,
            step,
        ):
            raise RuntimeError("Completion lease expired")
    if tracked:
        if failed_steps:
            # Keep the lease until expiry; the sweeper will retry only the
            # unfinished steps without racing an already queued duplicate.
            raise RuntimeError(f"Workflow completion failed: {', '.join(failed_steps)}")
        if not await db_client.checkpoint_workflow_completion(
            workflow_run_id, organization_id, token, "done"
        ):
            raise RuntimeError("Completion lease expired")
    logger.info("Completed workflow completion processing for run {}", workflow_run_id)


async def sweep_workflow_completions(_ctx) -> None:
    """Recover lost jobs, including runs whose extraction already finished."""
    from api.tasks.arq import enqueue_job
    from api.tasks.function_names import FunctionNames

    for run_id in await db_client.get_overdue_workflow_completions():
        try:
            # ARQ retains results for an hour, including failed jobs. A bounded
            # recovery generation bypasses that retention; the DB lease owns
            # deduplication against the original job and other recoveries.
            await enqueue_job(
                FunctionNames.PROCESS_WORKFLOW_COMPLETION,
                run_id,
                _job_id=f"workflow-completion-{run_id}-recovery-{int(time.time() // 600)}",
            )
        except Exception as exc:  # noqa: BLE001 - independent completion steps
            logger.warning(
                "Could not recover completion for run {} ({})",
                run_id,
                type(exc).__name__,
            )
