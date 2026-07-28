"""Agent-stream WebSocket endpoint.

A single ``/agent-stream/{provider_name}/{workflow_uuid}`` socket where a
caller can drive an agent run. The provider is part of the URL path;
provider-specific call metadata is read from that provider's stream protocol.

Auth: the workflow UUID itself acts as the identifier — no API key.
Routing: when ``/{provider_name}`` matches a telephony provider, we
dispatch to that provider's ``handle_external_websocket``.
"""

import uuid

from fastapi import APIRouter, WebSocket
from loguru import logger
from pipecat.utils.run_context import set_current_org_id, set_current_run_id
from starlette.websockets import WebSocketDisconnect

from api.db import db_client
from api.enums import CallType, WorkflowRunState
from api.services.call_concurrency import (
    CallConcurrencyLimitError,
    call_concurrency,
)
from api.services.quota_service import authorize_workflow_run_start
from api.services.telephony import registry as telephony_registry
from api.services.workflow.run_creation import prepare_workflow_run_inputs

router = APIRouter(prefix="/agent-stream")


@router.websocket("/{provider_name}/{workflow_uuid}")
async def agent_stream_websocket(
    websocket: WebSocket,
    provider_name: str,
    workflow_uuid: str,
):
    """Generic agent-stream WebSocket.

    ``provider_name`` is the registered telephony provider name
    (e.g. ``cloudonix``).
    """
    await websocket.accept()
    params = dict(websocket.query_params)
    params.pop("provider", None)

    spec = telephony_registry.get_optional(provider_name)
    if spec is None:
        logger.warning(f"agent-stream unknown provider: {provider_name}")
        await websocket.close(code=1008, reason=f"Unknown provider: {provider_name}")
        return

    workflow = await db_client.get_workflow_by_uuid_unscoped(workflow_uuid)
    if not workflow:
        logger.warning(f"agent-stream workflow {workflow_uuid} not found")
        await websocket.close(code=1008, reason="Workflow not found")
        return

    try:
        concurrency_slot = await call_concurrency.acquire_org_slot(
            workflow.organization_id,
            source=f"agent_stream:{provider_name}",
            timeout=0,
        )
    except CallConcurrencyLimitError:
        await websocket.close(code=1008, reason="Concurrent call limit reached")
        return

    numeric_suffix = int(str(uuid.uuid4()).replace("-", "")[:8], 16) % 100000000
    workflow_run_name = f"WR-AGS-{numeric_suffix:08d}"
    initial_context = {
        "provider": provider_name,
        "direction": "inbound",
    }
    try:
        run_inputs = await prepare_workflow_run_inputs(db_client, workflow)
        workflow_run = await db_client.create_workflow_run(
            workflow_run_name,
            workflow.id,
            provider_name,
            user_id=workflow.user_id,
            call_type=CallType.INBOUND,
            initial_context=initial_context,
            organization_id=workflow.organization_id,
            definition_id=run_inputs.definition_id,
        )
        await call_concurrency.bind_workflow_run(concurrency_slot, workflow_run.id)
    except Exception:
        await call_concurrency.release_slot(concurrency_slot)
        raise

    set_current_run_id(workflow_run.id)
    set_current_org_id(workflow.organization_id)

    quota_result = await authorize_workflow_run_start(
        workflow_id=workflow.id,
        organization_id=workflow.organization_id,
        workflow_run_id=workflow_run.id,
    )
    if not quota_result.has_quota:
        logger.warning(
            f"agent-stream quota exceeded for user {workflow.user_id}: "
            f"{quota_result.error_message}"
        )
        await call_concurrency.release_workflow_run_slot(workflow_run.id)
        await websocket.close(
            code=1008, reason=quota_result.error_message or "Quota exceeded"
        )
        return

    try:
        await db_client.update_workflow_run(
            run_id=workflow_run.id, state=WorkflowRunState.RUNNING.value
        )

        provider_instance = spec.provider_cls({})
        try:
            await provider_instance.handle_external_websocket(
                websocket,
                organization_id=workflow.organization_id,
                workflow_id=workflow.id,
                workflow_run_id=workflow_run.id,
                params=params,
            )
        except NotImplementedError as e:
            logger.warning(f"agent-stream provider {provider_name} not supported: {e}")
            try:
                await websocket.close(code=1011, reason=str(e))
            except RuntimeError:
                pass
        except WebSocketDisconnect as e:
            logger.info(f"agent-stream disconnected: code={e.code} reason={e.reason}")
        except Exception as e:
            logger.error(f"agent-stream error for run {workflow_run.id}: {e}")
            try:
                await websocket.close(1011, "Internal server error")
            except RuntimeError:
                pass
    finally:
        await call_concurrency.unregister_active_call(workflow_run.id)
