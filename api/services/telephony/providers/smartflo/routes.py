"""Smartflo telephony routes and webhooks."""

import json
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Request, Response, WebSocket
from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.db import db_client
from api.enums import CallType, WorkflowRunMode, WorkflowRunState
from api.services.call_concurrency import (
    CallConcurrencyLimitError,
    WorkflowRunSlotAlreadyBoundError,
    call_concurrency,
)
from api.services.quota_service import authorize_workflow_run_start
from api.services.telephony.providers.smartflo.agent_resolver import resolve_dograh_agent
from api.services.telephony.providers.smartflo.credential_resolver import (
    mask_phone_number,
    resolve_smartflo_credentials,
)
from api.services.telephony.providers.smartflo.provider import SmartfloProvider
from api.services.telephony.providers.smartflo.redis_state import (
    get_default_agent_id,
    get_did_mapping,
    get_smartflo_call_state,
    save_smartflo_call_state,
)
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)
from api.services.workflow.run_creation import prepare_workflow_run_inputs
from api.services.workflow_run_failure import mark_workflow_run_failed
from api.utils.common import get_backend_endpoints

router = APIRouter()


@router.post("/smartflo/call")
@router.post("/call")
async def make_smartflo_call(call_details: dict = Body(...)) -> Dict[str, Any]:
    """
    Make an outbound call using Smartflo Click-to-Call Support API.

    Resolves credentials hierarchically:
    Request Body -> Agent configuration -> Organization configuration -> Environment variables
    """
    agent_id = call_details.get("agent_id")
    recipient_phone_number = (
        call_details.get("recipient_phone_number")
        or call_details.get("customer_number")
        or call_details.get("to_number")
    )

    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")
    if not recipient_phone_number:
        raise HTTPException(status_code=400, detail="recipient_phone_number is required")

    # 1. Resolve Dograh workflow/agent
    try:
        workflow, organization_id = await resolve_dograh_agent(agent_id)
    except Exception as e:
        logger.error(f"[Smartflo] Failed to resolve agent {agent_id}: {e}")
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found") from e

    # 2. Resolve credentials with 4-tier fallback
    agent_config = workflow.workflow_configurations or {}
    try:
        api_key, did_number, jwt_token, api_domain = resolve_smartflo_credentials(
            call_details=call_details,
            agent_config=agent_config,
        )
    except ValueError as e:
        logger.error(f"[Smartflo] Credential resolution error: {e}")
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Masked audit log - NEVER log secrets
    logger.info(
        f"[Smartflo] Outbound call requested: agent_id={agent_id}, "
        f"workflow_id={workflow.id}, recipient={mask_phone_number(recipient_phone_number)}, "
        f"caller_id={did_number}"
    )

    # 3. Create workflow run in Dograh
    concurrency_slot = None
    try:
        concurrency_slot = await call_concurrency.acquire_org_slot(
            organization_id,
            source="smartflo_outbound",
            timeout=0,
        )
    except CallConcurrencyLimitError:
        raise HTTPException(status_code=429, detail="Concurrent call limit reached")

    numeric_suffix = int(str(uuid.uuid4()).replace("-", "")[:8], 16) % 100000000
    workflow_run_name = f"WR-SMARTFLO-{numeric_suffix:08d}"

    try:
        run_inputs = await prepare_workflow_run_inputs(
            db_client,
            workflow,
            initial_context={
                "phone_number": recipient_phone_number,
                "called_number": recipient_phone_number,
                "caller_number": did_number,
                "direction": "outbound",
                "provider": "smartflo",
                "agent_id": str(agent_id),
            },
            use_draft=False,
            include_template_context=True,
        )
        workflow_run = await db_client.create_workflow_run(
            workflow_run_name,
            workflow.id,
            WorkflowRunMode.SMARTFLO.value,
            user_id=workflow.user_id,
            call_type=CallType.OUTBOUND,
            initial_context=run_inputs.initial_context,
            organization_id=organization_id,
            definition_id=run_inputs.definition_id,
        )
        await call_concurrency.bind_workflow_run(concurrency_slot, workflow_run.id)
    except WorkflowRunSlotAlreadyBoundError:
        if concurrency_slot:
            await call_concurrency.release_slot(concurrency_slot)
        raise HTTPException(status_code=409, detail="Workflow run already active")
    except Exception as e:
        if concurrency_slot:
            await call_concurrency.release_slot(concurrency_slot)
        logger.error(f"[Smartflo] Error creating workflow run: {e}")
        raise HTTPException(status_code=500, detail="Failed to initialize call session") from e

    # Check quota
    quota_result = await authorize_workflow_run_start(
        workflow_id=workflow.id,
        organization_id=organization_id,
        workflow_run_id=workflow_run.id,
    )
    if not quota_result.has_quota:
        await mark_workflow_run_failed(
            workflow_run.id, quota_result.error_message or "Quota exceeded"
        )
        await call_concurrency.release_workflow_run_slot(workflow_run.id)
        raise HTTPException(status_code=402, detail=quota_result.error_message)

    # 4. Initiate call via Smartflo
    provider = SmartfloProvider({
        "click_to_call_api_key": api_key,
        "smartflo_did_number": did_number,
        "smartflo_jwt_token": jwt_token,
        "smartflo_api_domain": api_domain,
    })

    backend_endpoint, _ = await get_backend_endpoints()
    webhook_url = (
        f"{backend_endpoint}/smartflo_connect"
        f"?workflow_id={workflow.id}"
        f"&workflow_run_id={workflow_run.id}"
        f"&organization_id={organization_id}"
        f"&agent_id={agent_id}"
    )

    try:
        result = await provider.initiate_call(
            to_number=recipient_phone_number,
            webhook_url=webhook_url,
            workflow_run_id=workflow_run.id,
            from_number=did_number,
            agent_id=str(agent_id),
            workflow_id=workflow.id,
            organization_id=organization_id,
            smartflo_api_key=api_key,
            smartflo_jwt_token=jwt_token,
            smartflo_api_domain=api_domain,
        )
    except Exception as e:
        await mark_workflow_run_failed(workflow_run.id, f"Smartflo API call failed: {e}")
        await call_concurrency.release_workflow_run_slot(workflow_run.id)
        logger.error(f"[Smartflo] Call initiation failed: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to place call via Smartflo: {e}") from e

    # Update workflow run gathered_context
    gathered = {
        "provider": "smartflo",
        "agent_id": str(agent_id),
        "customer_number": recipient_phone_number,
        "caller_id": did_number,
        **(result.provider_metadata or {}),
    }
    await db_client.update_workflow_run(
        run_id=workflow_run.id,
        gathered_context=gathered,
    )

    ref_id = result.provider_metadata.get("smartflo_ref_id") or result.call_id
    call_id = result.call_id

    # Persist in Redis mapping for subsequent streaming callbacks
    await save_smartflo_call_state(
        ref_id=ref_id,
        call_id=call_id,
        customer_number=recipient_phone_number,
        state={
            "agent_id": str(agent_id),
            "workflow_id": workflow.id,
            "workflow_run_id": workflow_run.id,
            "organization_id": organization_id,
            "customer_number": recipient_phone_number,
            "caller_id": did_number,
            "ref_id": ref_id,
            "call_id": call_id,
            "status": result.status,
        },
    )

    return {
        "status": "success",
        "message": "Call initiated successfully",
        "ref_id": ref_id,
        "call_id": call_id,
        "workflow_run_id": workflow_run.id,
        "agent_id": str(agent_id),
        "status_code": 200,
    }


@router.api_route(
    "/smartflo_connect",
    methods=["GET", "POST"],
)
async def smartflo_connect(request: Request) -> Response:
    """
    Dynamic endpoint called by Smartflo Voice Bot to obtain WebSocket streaming URL.

    Resolves: agent_id, callId, toNumber, fromNumber, custom_identifier
    from:
    1. Query parameters
    2. POST body
    3. Redis call state mapping
    """
    params = dict(request.query_params)
    body_data: Dict[str, Any] = {}

    if request.method == "POST":
        try:
            content_type = request.headers.get("content-type", "")
            if "json" in content_type:
                body_data = await request.json()
            elif "form" in content_type:
                form = await request.form()
                body_data = dict(form)
        except Exception as e:
            logger.debug(f"[Smartflo] Could not parse request body: {e}")

    # Extract identifiers
    call_id = (
        params.get("callId")
        or params.get("call_id")
        or body_data.get("callId")
        or body_data.get("call_id")
        or body_data.get("ref_id")
        or params.get("ref_id")
    )
    custom_identifier = (
        params.get("custom_identifier")
        or body_data.get("custom_identifier")
        or params.get("customIdentifier")
        or body_data.get("customIdentifier")
    )
    to_number = (
        params.get("toNumber")
        or params.get("to")
        or body_data.get("toNumber")
        or body_data.get("customer_number")
        or body_data.get("to")
    )
    from_number = (
        params.get("fromNumber")
        or params.get("from")
        or body_data.get("fromNumber")
        or body_data.get("caller_id")
        or body_data.get("from")
    )
    agent_id = params.get("agent_id") or body_data.get("agent_id") or custom_identifier

    # Check if this DID is mapped in Redis to a specific campaign/agent (e.g. did_map:{toNumber})
    if not agent_id and to_number:
        agent_id = await get_did_mapping(to_number)

    # Fallback to default agent in Redis if still not resolved
    if not agent_id:
        agent_id = await get_default_agent_id()

    workflow_id_str = params.get("workflow_id") or body_data.get("workflow_id")
    workflow_run_id_str = params.get("workflow_run_id") or body_data.get("workflow_run_id")
    organization_id_str = params.get("organization_id") or body_data.get("organization_id")

    # If IDs missing, lookup in Redis by call_id, custom_identifier, or to_number
    cached_state = None
    for lookup_key in (call_id, custom_identifier, to_number):
        if lookup_key:
            cached_state = await get_smartflo_call_state(str(lookup_key))
            if cached_state:
                break

    if cached_state:
        workflow_id_str = workflow_id_str or cached_state.get("workflow_id")
        workflow_run_id_str = workflow_run_id_str or cached_state.get("workflow_run_id")
        organization_id_str = organization_id_str or cached_state.get("organization_id")
        agent_id = agent_id or cached_state.get("agent_id")

    # If workflow still not resolved, try resolving via agent_id
    if (not workflow_id_str or not organization_id_str) and agent_id:
        try:
            workflow, org_id = await resolve_dograh_agent(str(agent_id))
            workflow_id_str = workflow.id
            organization_id_str = org_id
            
            # If this is a direct incoming connect from Smartflo, initialize a run
            if not workflow_run_id_str:
                from api.services.workflow.run_creation import prepare_workflow_run_inputs
                run_inputs = await prepare_workflow_run_inputs(
                    db_client,
                    workflow,
                    initial_context={
                        "phone_number": to_number or "",
                        "called_number": to_number or "",
                        "caller_number": from_number or "",
                        "direction": "inbound",
                        "provider": "smartflo",
                        "agent_id": str(agent_id),
                    },
                    use_draft=False,
                    include_template_context=True,
                )
                inbound_run = await db_client.create_workflow_run(
                    f"WR-SMARTFLO-IN-{int(time.time()) % 10000000}",
                    workflow.id,
                    WorkflowRunMode.SMARTFLO.value,
                    user_id=workflow.user_id,
                    call_type=CallType.INBOUND,
                    initial_context=run_inputs.initial_context,
                    organization_id=org_id,
                    definition_id=run_inputs.definition_id,
                )
                workflow_run_id_str = str(inbound_run.id)
                logger.info(f"[Smartflo] Created dynamic inbound run {workflow_run_id_str} for agent {agent_id}")
        except Exception as e:
            logger.warning(f"[Smartflo] Agent resolution in connect endpoint failed: {e}")

    # Dynamically determine WebSocket host using incoming request headers
    host_header = request.headers.get("x-forwarded-host") or request.headers.get("host")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    if host_header:
        ws_scheme = "wss" if proto == "https" else "ws"
        ws_host = f"{ws_scheme}://{host_header}"
    else:
        backend_endpoint, _ = await get_backend_endpoints()
        ws_host = backend_endpoint.replace("https://", "wss://").replace("http://", "ws://")

    if workflow_run_id_str:
        ws_url = f"{ws_host}/stream?token={workflow_run_id_str}"
    else:
        ws_url = f"{ws_host}/stream"

    logger.info(
        f"[Smartflo] Resolved connect streaming endpoint: call_id={call_id}, "
        f"run_id={workflow_run_id_str}, ws_url={ws_url}"
    )

    response_payload = {
        "success": True,
        "wss_url": ws_url,
        "url": ws_url,
        "ws_url": ws_url,
        "status": "success",
    }

    return Response(
        content=json.dumps(response_payload),
        media_type="application/json",
        status_code=200,
    )


@router.post("/events")
@router.post("/smartflo/events")
@router.post("/status-callback/{workflow_run_id}")
async def handle_smartflo_events(
    request: Request,
    workflow_run_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Webhook handler for Smartflo call lifecycle events.
    (initiated, ringing, answered, connected, completed, failed, busy, no-answer, cancelled)
    """
    try:
        content_type = request.headers.get("content-type", "")
        if "json" in content_type:
            data = await request.json()
        else:
            form = await request.form()
            data = dict(form)
    except Exception as e:
        logger.warning(f"[Smartflo] Could not parse event payload: {e}")
        return {"status": "ignored"}

    logger.info(f"[Smartflo] Webhook event received: {data}")

    call_id = str(data.get("call_id") or data.get("ref_id") or data.get("id") or "")
    run_id = workflow_run_id or data.get("workflow_run_id") or data.get("custom_identifier")

    # If run_id not in params, resolve from Redis
    if not run_id and call_id:
        cached = await get_smartflo_call_state(call_id)
        if cached:
            run_id = cached.get("workflow_run_id")

    if run_id:
        try:
            run_id_int = int(run_id)
            set_current_run_id(run_id_int)
            provider = SmartfloProvider({})
            parsed = provider.parse_status_callback(data)

            callback_req = StatusCallbackRequest(
                call_id=parsed["call_id"] or call_id,
                status=parsed["status"],
                from_number=parsed.get("from_number"),
                to_number=parsed.get("to_number"),
                duration=str(parsed.get("duration") or ""),
                extra=data,
            )
            await _process_status_update(run_id_int, callback_req)
        except Exception as e:
            logger.warning(f"[Smartflo] Error updating status for run {run_id}: {e}")

    return {"status": "received"}


@router.websocket("/smartflo/stream")
@router.websocket("/smartflo/stream/{workflow_run_id}")
@router.websocket("/stream")
@router.websocket("/stream/{workflow_run_id}")
async def smartflo_direct_stream(
    websocket: WebSocket,
    workflow_run_id: Optional[int] = None,
):
    """Direct Smartflo audio streaming WebSocket handler."""
    await websocket.accept()

    run_id = workflow_run_id
    if not run_id:
        qp = websocket.query_params
        run_id = qp.get("workflow_run_id") or qp.get("run_id") or qp.get("token")

    if not run_id:
        logger.info("[Smartflo] WebSocket connected in test/probe mode (no workflow_run_id). Socket accepted.")
        try:
            while True:
                msg = await websocket.receive_text()
                await websocket.send_text(json.dumps({"status": "ready", "event": "pong"}))
        except Exception:
            return

    try:
        run_id_int = int(run_id)
        workflow_run = await db_client.get_workflow_run_by_id(run_id_int)
        if not workflow_run:
            await websocket.close(code=4404, reason="Workflow run not found")
            return

        workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
        if not workflow:
            logger.warning(f"[Smartflo] Workflow {workflow_run.workflow_id} not found for run {run_id_int}")
            await websocket.close(code=4404, reason="Workflow not found")
            return

        await db_client.update_workflow_run(
            run_id=run_id_int,
            state=WorkflowRunState.RUNNING.value,
        )

        provider = SmartfloProvider({})
        await provider.handle_websocket(
            websocket=websocket,
            workflow_id=workflow.id,
            organization_id=workflow.organization_id,
            workflow_run_id=run_id_int,
        )
    except Exception as e:
        logger.error(f"[Smartflo] Direct stream error: {e}", exc_info=True)
        try:
            await websocket.close(code=1011, reason="Stream handler error")
        except Exception:
            pass
