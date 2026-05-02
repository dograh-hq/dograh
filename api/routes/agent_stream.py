"""Agent-stream WebSocket endpoint.

A single ``/agent-stream/{workflow_uuid}`` socket where a caller can drive
an agent run by passing everything inline in the query string — including
provider credentials. The standard ``/telephony/ws/...`` path requires a
``TelephonyConfigurationModel`` row stored in the org; this one does not.

Auth: the workflow UUID itself acts as the identifier — no API key.
Routing: when ``?provider=<registered>`` matches a telephony provider, we
dispatch to that provider's ``handle_external_websocket``. The raw-audio
branch (no provider) is reserved for a future protocol decision and
currently rejects with 1011.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket
from loguru import logger
from starlette.websockets import WebSocketDisconnect

from api.db import db_client
from api.enums import CallType, WorkflowRunState
from api.services.quota_service import check_dograh_quota_by_user_id
from api.services.telephony import registry as telephony_registry
from pipecat.utils.run_context import set_current_org_id, set_current_run_id

router = APIRouter(prefix="/agent-stream")


@router.websocket("/{workflow_uuid}")
async def agent_stream_websocket(
    websocket: WebSocket,
    workflow_uuid: str,
):
    """Generic agent-stream WebSocket.

    Query params:
        provider: registered telephony provider name (e.g. ``cloudonix``)
        from / to / callId: call metadata persisted on the workflow run
        ...: provider-specific credentials/identifiers (e.g. ``session``,
             ``AccountSid``, ``CallSid`` for cloudonix)

    Without ``provider`` the raw-audio branch is currently not implemented.
    """
    await websocket.accept()
    params = dict(websocket.query_params)
    provider_name: Optional[str] = params.get("provider")

    if not provider_name:
        logger.warning(
            f"agent-stream raw audio branch not yet supported "
            f"(workflow_uuid={workflow_uuid})"
        )
        await websocket.close(code=1011, reason="Raw audio stream not yet implemented")
        return

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

    quota_result = await check_dograh_quota_by_user_id(
        workflow.user_id, workflow_id=workflow.id
    )
    if not quota_result.has_quota:
        logger.warning(
            f"agent-stream quota exceeded for user {workflow.user_id}: "
            f"{quota_result.error_message}"
        )
        await websocket.close(
            code=1008, reason=quota_result.error_message or "Quota exceeded"
        )
        return

    numeric_suffix = int(str(uuid.uuid4()).replace("-", "")[:8], 16) % 100000000
    workflow_run_name = f"WR-AGS-{numeric_suffix:08d}"
    call_id = params.get("callId") or params.get("CallSid")
    initial_context = {
        **(workflow.template_context_variables or {}),
        "provider": provider_name,
        "caller_number": params.get("from"),
        "called_number": params.get("to"),
        "direction": "inbound",
    }
    workflow_run = await db_client.create_workflow_run(
        workflow_run_name,
        workflow.id,
        provider_name,
        user_id=workflow.user_id,
        call_type=CallType.INBOUND,
        initial_context=initial_context,
        gathered_context={"call_id": call_id} if call_id else {},
        logs={
            "inbound_webhook": {
                "domain": params.get("Domain"),
            },
        },
    )

    set_current_run_id(workflow_run.id)
    set_current_org_id(workflow.organization_id)

    await db_client.update_workflow_run(
        run_id=workflow_run.id, state=WorkflowRunState.RUNNING.value
    )

    provider_instance = spec.provider_cls({})
    try:
        await provider_instance.handle_external_websocket(
            websocket,
            organization_id=workflow.organization_id,
            workflow_id=workflow.id,
            user_id=workflow.user_id,
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
