import random

from loguru import logger

from api.db import db_client
from api.enums import WorkflowRunMode
from api.services.pipecat.run_pipeline import run_pipeline_ari_stasis
from api.services.telephony.stasis_rtp_connection import StasisRTPConnection
from pipecat.utils.run_context import set_current_run_id


async def on_stasis_call(call: StasisRTPConnection, call_context_vars: dict):
    workflow_id = call_context_vars.get("workflow_id") or call_context_vars.get(
        "campaign_id"
    )
    user_id = call_context_vars.get("user_id")

    assert workflow_id is not None
    assert user_id is not None

    try:
        workflow_id = int(workflow_id)
        user_id = int(user_id)
    except ValueError:
        logger.error(f"Invalid workflow ID or user ID: {workflow_id} or {user_id}")
        return

    workflow_run_name = f"WR-ARI-{random.randint(1000, 9999)}"
    workflow_run = await db_client.create_workflow_run(
        workflow_run_name, workflow_id, WorkflowRunMode.STASIS.value, user_id
    )

    set_current_run_id(workflow_run.id)

    # Store the workflow_run_id in the connection for later use
    call.workflow_run_id = workflow_run.id

    # Connect channelID with Workflow run ID in logs
    logger.info(
        f"channelID: {call.caller_channel_id} run_id: {workflow_run.id} "
        f"Received call for workflow ID {workflow_id}, user ID {user_id}"
    )
    await run_pipeline_ari_stasis(
        call, workflow_id, workflow_run.id, user_id, call_context_vars
    )
