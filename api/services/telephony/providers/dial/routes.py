"""Dial routes: the Self-Hosted audio media socket and call-status webhooks.

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony``.

Dial opens one WebSocket per call to the Self-Hosted URL set on save,
``.../dial/ws/{account_id}``, with the call id appended. The first frame,
``call_connected``, says whether the call is outbound (placed by
``DialProvider.initiate_call``, so a workflow run already holds its call id)
or inbound (someone called a Dial number, so the run is created here, the
same way ``/inbound/run`` does for webhook-driven carriers).

Protocol: https://docs.getdial.ai/api-reference/self-hosted-audio-protocol
"""

import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.db import db_client
from api.enums import CallType, WorkflowRunState
from api.services.call_concurrency import (
    CallConcurrencyLimitError,
    call_concurrency,
)
from api.services.quota_service import authorize_workflow_run_start
from api.services.telephony.factory import (
    get_telephony_provider_by_id,
    get_telephony_provider_for_run,
)
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)
from api.services.workflow.run_creation import prepare_workflow_run_inputs
from api.services.workflow_run_failure import mark_workflow_run_failed

from .provider import PROVIDER_NAME, DialProvider
from .signatures import SIGNATURE_HEADER, verify_dial_signature
from .websocket import DialWebSocket

router = APIRouter()

# Dial sends call_connected immediately after the socket opens.
_HANDSHAKE_TIMEOUT_SECONDS = 10

# An outbound call can be answered before the run that placed it has stored
# the returned call id. Wait briefly for that write rather than drop the call.
_OUTBOUND_RUN_LOOKUP_ATTEMPTS = 20
_OUTBOUND_RUN_LOOKUP_INTERVAL_SECONDS = 0.25

_STATUS_EVENT_TYPES = frozenset({"call.ended", "call.status_changed"})


async def _hang_up(socket: DialWebSocket, code: int, reason: str) -> None:
    """End the Dial call and close the socket.

    Only used once the connection is authenticated: an ``end_call`` is what
    keeps Dial from reconnecting and ringing on, so a rejected call ends for
    the caller instead of hanging in silence.
    """
    try:
        await socket.send_json_frame({"type": "end_call"})
    except Exception:
        pass
    await socket.close(code=code, reason=reason)


@router.websocket("/dial/ws/{account_id}/{call_id}")
async def dial_media_socket(websocket: WebSocket, account_id: str, call_id: str):
    signature = websocket.headers.get(SIGNATURE_HEADER)
    await websocket.accept()

    socket = DialWebSocket(websocket)
    socket.start()
    try:
        frame = await socket.receive_json_frame(_HANDSHAKE_TIMEOUT_SECONDS)
        if (
            not frame
            or frame.get("type") != "call_connected"
            or frame.get("call_id") != call_id
        ):
            logger.warning(f"[Dial] call {call_id}: no valid call_connected frame")
            await socket.close(code=4400, reason="Expected call_connected")
            return

        # Authenticate before anything else, against every configuration that
        # claims this Dial account. From here on a rejection can end the call
        # in-band instead of leaving Dial to retry a socket that will not work.
        configs = await _configs_for_account(account_id)
        if not any(
            verify_dial_signature(
                (c.credentials or {}).get("audio_signing_secret"), signature, call_id
            )
            for c in configs
        ):
            logger.warning(f"[Dial] call {call_id}: unauthenticated media socket")
            await socket.close(code=4401, reason="Unauthorized")
            return

        if frame.get("reconnect"):
            # The pipeline that served this call died with the old socket;
            # there is nothing to resume, so end the call cleanly.
            logger.warning(f"[Dial] call {call_id}: reconnect cannot be resumed")
            await _hang_up(socket, 4409, "Call can no longer be resumed")
            return

        direction = frame.get("direction")
        if direction == "outbound":
            await _serve_outbound(socket, account_id, call_id, signature)
        elif direction == "inbound":
            await _serve_inbound(socket, account_id, call_id, signature, frame)
        else:
            logger.warning(f"[Dial] call {call_id}: unknown direction {direction!r}")
            await _hang_up(socket, 4400, "Unknown call direction")
    except WebSocketDisconnect as e:
        logger.info(f"[Dial] call {call_id}: socket disconnected (code={e.code})")
    except Exception as e:
        logger.opt(exception=True).error(
            f"[Dial] call {call_id}: media socket error: {e}"
        )
        try:
            await socket.close(code=1011, reason="Internal server error")
        except Exception:
            pass
    finally:
        await socket.stop()


async def _provider_for_run(run) -> Optional[DialProvider]:
    if (run.initial_context or {}).get("provider") != PROVIDER_NAME:
        return None
    provider = await get_telephony_provider_for_run(run, run.workflow.organization_id)
    return provider if isinstance(provider, DialProvider) else None


async def _serve_outbound(
    socket: DialWebSocket, account_id: str, call_id: str, signature: Optional[str]
) -> None:
    run = None
    for _ in range(_OUTBOUND_RUN_LOOKUP_ATTEMPTS):
        run = await db_client.get_workflow_run_by_call_id(call_id)
        if run:
            break
        await asyncio.sleep(_OUTBOUND_RUN_LOOKUP_INTERVAL_SECONDS)

    if not run:
        # Not a call Dograh placed (e.g. one made from the Dial dashboard
        # while Self-Hosted points here): nothing would drive it.
        logger.warning(f"[Dial] outbound call {call_id} has no workflow run")
        await _hang_up(socket, 4404, "Workflow run not found")
        return

    set_current_run_id(run.id)
    provider = await _provider_for_run(run)
    # The run's own configuration must own this account and this signature,
    # not merely some configuration of the account (two orgs could share it).
    if (
        provider is None
        or provider.account_id != account_id
        or not provider.verify_media_socket_signature(call_id, signature)
    ):
        logger.warning(
            f"[Dial] outbound call {call_id}: run belongs to another account"
        )
        await _hang_up(socket, 4401, "Unauthorized")
        return

    if run.state != WorkflowRunState.INITIALIZED.value:
        logger.warning(f"[Dial] run {run.id} not in initialized state: {run.state}")
        await _hang_up(socket, 4409, "Workflow run not available for connection")
        return

    await _run_pipeline(
        socket,
        workflow_id=run.workflow_id,
        workflow_run_id=run.id,
        organization_id=run.workflow.organization_id,
        call_id=call_id,
    )


async def _serve_inbound(
    socket: DialWebSocket,
    account_id: str,
    call_id: str,
    signature: Optional[str],
    frame: Dict[str, Any],
) -> None:
    normalized = DialProvider.parse_inbound_webhook({**frame, "account_id": account_id})

    match = await db_client.find_inbound_route_by_account(
        provider=PROVIDER_NAME,
        account_id_field="account_id",
        account_id=account_id,
        to_number=normalized.to_number,
    )
    if not match:
        logger.warning(
            f"[Dial] inbound call {call_id}: no active number {normalized.to_number} "
            f"on a Dial configuration for account {account_id}"
        )
        await _hang_up(socket, 4404, "Phone number not configured")
        return

    config, phone_row = match
    provider = await get_telephony_provider_by_id(config.id, config.organization_id)
    if not isinstance(
        provider, DialProvider
    ) or not provider.verify_media_socket_signature(call_id, signature):
        logger.warning(f"[Dial] inbound call {call_id}: signature verification failed")
        await _hang_up(socket, 4401, "Unauthorized")
        return

    if await db_client.get_workflow_run_by_call_id(call_id):
        logger.warning(f"[Dial] inbound call {call_id} already has a workflow run")
        await _hang_up(socket, 4409, "Call already handled")
        return

    workflow_id = phone_row.inbound_workflow_id
    workflow = (
        await db_client.get_workflow(
            workflow_id, organization_id=config.organization_id
        )
        if workflow_id
        else None
    )
    if not workflow:
        logger.warning(
            f"[Dial] inbound call {call_id}: number {normalized.to_number} has no "
            "inbound workflow"
        )
        await _hang_up(socket, 4404, "No inbound workflow for this number")
        return

    try:
        slot = await call_concurrency.acquire_org_slot(
            config.organization_id, source=f"inbound:{PROVIDER_NAME}", timeout=0
        )
    except CallConcurrencyLimitError:
        logger.warning(f"[Dial] org {config.organization_id} is at its call limit")
        await _hang_up(socket, 4429, "Concurrent call limit reached")
        return

    workflow_run_id: Optional[int] = None
    try:
        run_inputs = await prepare_workflow_run_inputs(db_client, workflow)
        suffix = int(uuid.uuid4().hex[:8], 16) % 100000000
        workflow_run = await db_client.create_workflow_run(
            f"WR-TEL-IN-{suffix:08d}",
            workflow.id,
            PROVIDER_NAME,
            user_id=workflow.user_id,
            call_type=CallType.INBOUND,
            initial_context={
                "caller_number": normalized.from_number,
                "called_number": normalized.to_number,
                "direction": "inbound",
                "provider": PROVIDER_NAME,
                "telephony_configuration_id": config.id,
            },
            gathered_context={"call_id": call_id},
            logs={
                "inbound_webhook": {
                    "account_id": account_id,
                    "from_phone_number_id": phone_row.id,
                    "raw_webhook_data": frame,
                },
            },
            organization_id=config.organization_id,
            definition_id=run_inputs.definition_id,
            use_draft=run_inputs.use_draft,
        )
        workflow_run_id = workflow_run.id
        set_current_run_id(workflow_run_id)
        await call_concurrency.bind_workflow_run(slot, workflow_run_id)

        quota = await authorize_workflow_run_start(
            workflow_id=workflow.id,
            organization_id=config.organization_id,
            workflow_run_id=workflow_run_id,
        )
        if not quota.has_quota:
            await mark_workflow_run_failed(
                workflow_run_id, quota.error_message or "Quota exceeded"
            )
            await call_concurrency.release_workflow_run_slot(workflow_run_id)
            await _hang_up(socket, 4402, "Quota exceeded")
            return
    except Exception as e:
        if workflow_run_id:
            await mark_workflow_run_failed(
                workflow_run_id, f"Inbound call failed to start: {e}"
            )
            await call_concurrency.release_workflow_run_slot(workflow_run_id)
        else:
            await call_concurrency.release_slot(slot)
        await _hang_up(socket, 1011, "Inbound call failed to start")
        raise

    logger.info(
        f"[Dial] inbound call {call_id} from {normalized.from_number} -> "
        f"run {workflow_run_id} (org {config.organization_id})"
    )
    await _run_pipeline(
        socket,
        workflow_id=workflow.id,
        workflow_run_id=workflow_run_id,
        organization_id=config.organization_id,
        call_id=call_id,
    )


async def _run_pipeline(
    socket: DialWebSocket,
    *,
    workflow_id: int,
    workflow_run_id: int,
    organization_id: int,
    call_id: str,
) -> None:
    from api.services.pipecat.run_pipeline import run_pipeline_telephony

    # run_pipeline_telephony moves the run to 'running' and releases its
    # concurrency slot when the pipeline ends.
    await run_pipeline_telephony(
        socket,
        provider_name=PROVIDER_NAME,
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        organization_id=organization_id,
        call_id=call_id,
        transport_kwargs={"call_id": call_id},
    )


async def _configs_for_account(account_id: str) -> List[Any]:
    configs = await db_client.list_active_telephony_configurations_by_provider(
        PROVIDER_NAME
    )
    return [c for c in configs if (c.credentials or {}).get("account_id") == account_id]


@router.post("/dial/events/{account_id}")
async def dial_events(account_id: str, request: Request):
    """Receive Dial webhook deliveries; ``call.ended`` closes the workflow run.

    Calls that never connect (busy, no answer, failed) never open a media
    socket, so this is the only signal that their run is over.

    Unsigned or mis-signed deliveries get the same 404 as an unknown account,
    so the endpoint reveals nothing about which accounts exist.
    """
    try:
        raw_body = (await request.body()).decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Body is not valid UTF-8")

    signature = request.headers.get(SIGNATURE_HEADER)
    configs = await _configs_for_account(account_id)
    if not any(
        verify_dial_signature(
            (c.credentials or {}).get("webhook_secret"), signature, raw_body
        )
        for c in configs
    ):
        logger.warning(f"[Dial] rejected unsigned or mis-signed event for {account_id}")
        raise HTTPException(status_code=404, detail="Not found")

    try:
        event = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Body is not valid JSON")

    event_type = event.get("type")
    if event_type not in _STATUS_EVENT_TYPES:
        # webhook.ping and any event types added later: acknowledged, ignored.
        return {"status": "ignored"}

    call_id = (event.get("data") or {}).get("callId")
    run = await db_client.get_workflow_run_by_call_id(call_id) if call_id else None
    if not run or (run.initial_context or {}).get("provider") != PROVIDER_NAME:
        return {"status": "ignored"}

    set_current_run_id(run.id)
    provider = await _provider_for_run(run)
    if provider is None or provider.account_id != account_id:
        return {"status": "ignored"}

    parsed = provider.parse_status_callback(event)
    await _process_status_update(
        run.id,
        StatusCallbackRequest(
            call_id=parsed["call_id"],
            status=parsed["status"],
            from_number=parsed.get("from_number"),
            to_number=parsed.get("to_number"),
            direction=parsed.get("direction"),
            duration=parsed.get("duration"),
            extra=parsed.get("extra", {}),
        ),
    )
    return {"status": "success"}
