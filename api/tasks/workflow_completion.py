from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.services.call_persistence import persist_workflow_run_call_data
from api.services.telephony.external_pbx_writeback import (
    sync_external_pbx_call_record,
)
from api.services.workflow_run_billing import (
    report_completed_workflow_run_platform_usage,
)
from api.tasks.function_names import FunctionNames
from api.tasks.run_integrations import run_integrations_post_workflow_run


async def process_workflow_completion(
    _ctx,
    workflow_run_id: int,
):
    """Process workflow completion: run integrations and report billing.

    Recording/transcript uploads happen in the pipeline process itself
    (api/services/workflow_run_artifacts.py) before this job is enqueued,
    so this task needs no shared filesystem with the web tier.

    Args:
        _ctx: ARQ context (unused)
        workflow_run_id: The workflow run ID
    """
    run_id = str(workflow_run_id)
    set_current_run_id(run_id)

    logger.info(f"Processing workflow completion for run {workflow_run_id}")

    # Project the canonical workflow run into durable utterance, score, and
    # memory records inside the worker. This keeps PostgreSQL work off the live
    # call teardown path while ensuring integrations see the completed snapshot.
    try:
        await persist_workflow_run_call_data(_ctx, workflow_run_id)
    except Exception as e:
        logger.error(
            "Error persisting completed call data for workflow {} ({})",
            workflow_run_id,
            type(e).__name__,
        )
        try:
            from api.tasks.arq import enqueue_job

            await enqueue_job(
                FunctionNames.PERSIST_CALL_DATA,
                workflow_run_id,
                _job_id=f"persist-call-data-{workflow_run_id}",
            )
        except Exception:
            logger.error(
                "Unable to enqueue call data retry for workflow {}",
                workflow_run_id,
            )

    # Run integrations including QA analysis (after uploads are complete)
    try:
        await run_integrations_post_workflow_run(_ctx, workflow_run_id)
    except Exception as e:
        logger.error(f"Error running integrations for workflow {workflow_run_id}: {e}")

    # Notify MPS after completion. MPS owns credit accounting.
    try:
        await report_completed_workflow_run_platform_usage(workflow_run_id)
    except Exception as e:
        logger.error(
            f"Error reporting platform usage for workflow {workflow_run_id}: {e}"
        )

    # Deliberately last. The write-back reads the run's gathered context to
    # resolve the workflow's mapped lead fields, and on an abrupt hangup the
    # final variable extraction can still be in flight when this job starts --
    # it is an LLM call on a dead socket. The steps above are themselves slow
    # (QA is another LLM round-trip), so running after them gives that
    # extraction time to land instead of writing the lead without it. No-ops
    # for runs that never had a PBX leg.
    try:
        await sync_external_pbx_call_record(workflow_run_id)
    except Exception as e:
        logger.error(
            f"Error writing back to external PBX for workflow {workflow_run_id}: {e}"
        )

    logger.info(f"Completed workflow completion processing for run {workflow_run_id}")
