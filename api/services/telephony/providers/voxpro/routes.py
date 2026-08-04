"""VoxPro telephony routes (transfer-result callback).

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via the provider
registry — see ProviderSpec / ``_mount_provider_routers``.

VoxPro is call-control, so it has no HTTP status-callback route (``WEBHOOK_ENDPOINT
= None``). The one webhook it *does* need is the transfer-result callback: a blind
carrier transfer completes asynchronously in Asterisk, and the shared transfer flow
blocks on ``wait_for_transfer_completion`` until a ``TransferEvent`` is published for
the transfer_id. ``VoxProProvider.transfer_call`` hands the connector a ``result_url``
pointing here (same shape as Telnyx's transfer ``webhook_url``); the connector POSTs
the outcome and this route publishes the completion event that unblocks the wait.
"""

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from api.db import db_client
from api.services.telephony.call_transfer_manager import get_call_transfer_manager
from api.services.telephony.factory import get_telephony_provider_for_run
from api.services.telephony.transfer_event_protocol import (
    TransferContext,
    TransferEvent,
    TransferEventType,
)

router = APIRouter()

# Connector outcomes that mean the destination is on the line and the caller has
# been handed over. Anything else is treated as a failed transfer so the LLM can
# recover and tell the user. Kept permissive so the connector can report in its
# own vocabulary without breaking the contract.
_SUCCESS_OUTCOMES = {"answered", "completed", "success", "bridged", "transferred"}


def build_transfer_event(
    transfer_id: str, context: Optional[TransferContext], body: Dict[str, Any]
) -> TransferEvent:
    """Map a connector transfer-result payload to a TransferEvent.

    Pure (no I/O) so the outcome→event mapping is unit-testable without Redis.
    A success publishes ``DESTINATION_ANSWERED`` (the shared flow then ends the
    Dograh pipeline leg — the caller is already with the destination in Asterisk,
    so a blind transfer needs no conference join); anything else publishes
    ``TRANSFER_FAILED`` with a reason.
    """
    outcome = str(
        body.get("outcome") or body.get("status") or body.get("state") or ""
    ).lower()
    original_call_sid = context.original_call_sid if context else ""
    conference_name = context.conference_name if context else None
    transfer_call_sid = body.get("call_sid") or body.get("transfer_call_sid")

    if outcome in _SUCCESS_OUTCOMES:
        return TransferEvent(
            type=TransferEventType.DESTINATION_ANSWERED,
            transfer_id=transfer_id,
            original_call_sid=original_call_sid,
            transfer_call_sid=transfer_call_sid,
            conference_name=conference_name,
            status="success",
            action="destination_answered",
            message="VoxPro transfer connected — destination answered.",
        )

    reason = str(body.get("reason") or outcome or "call_failed")
    return TransferEvent(
        type=TransferEventType.TRANSFER_FAILED,
        transfer_id=transfer_id,
        original_call_sid=original_call_sid,
        transfer_call_sid=transfer_call_sid,
        conference_name=conference_name,
        status="transfer_failed",
        action="transfer_failed",
        reason=reason,
        message=f"VoxPro transfer did not connect (reason={reason}).",
        end_call=True,
    )


async def _verify_transfer_result_signature(
    context, request: "Request", raw_body: bytes, body: Dict[str, Any]
) -> bool:
    """Check X-VoxPro-Signature against the credentials used for this transfer.

    Resolved through the run rather than a header-supplied tenant so a caller
    cannot nominate which key verifies its own message.
    """
    signature = ""
    for key, value in request.headers.items():
        if key.lower() == "x-voxpro-signature":
            signature = value
            break
    if not signature:
        logger.warning("[VoxPro] transfer-result missing X-VoxPro-Signature")
        return False

    workflow_run_id = getattr(context, "workflow_run_id", None)
    if not workflow_run_id:
        logger.warning(
            "[VoxPro] transfer context has no workflow_run_id; cannot authenticate"
        )
        return False

    try:
        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
        if workflow_run is None or workflow_run.workflow is None:
            logger.warning(
                f"[VoxPro] workflow run {workflow_run_id} not found for transfer-result"
            )
            return False
        provider = await get_telephony_provider_for_run(
            workflow_run, workflow_run.workflow.organization_id
        )
    except Exception as e:  # noqa: BLE001 — auth must fail closed.
        logger.error(f"[VoxPro] could not resolve credentials to verify callback: {e}")
        return False

    verified = await provider.verify_webhook_signature(
        str(request.url), body, signature, body=raw_body.decode("utf-8", "replace")
    )
    if not verified:
        logger.warning("[VoxPro] transfer-result signature mismatch")
    return verified


@router.post("/voxpro/transfer-result/{transfer_id}")
async def handle_voxpro_transfer_result(transfer_id: str, request: Request):
    """Publish the completion event for a blind transfer.

    Authenticated with ``X-VoxPro-Signature``: an HMAC-SHA256 of the raw body
    keyed on the tenant's API key, the same scheme the connector uses for its
    inbound webhook. The unguessable ``transfer_id`` alone is not treated as
    proof — anything that learned one could otherwise post
    ``{"outcome": "answered"}`` and make the waiting workflow end its pipeline
    leg as if the destination had picked up, with no carrier confirmation.

    The signing key is resolved per transfer: TransferContext carries the
    ``workflow_run_id``, which yields the run's telephony configuration and so
    the credentials this transfer was actually initiated with. A callback whose
    context has expired cannot be authenticated (and has nothing left to
    unblock), so it is rejected rather than published.
    """
    raw_body = await request.body()
    try:
        body = json.loads(raw_body) if raw_body else {}
        if not isinstance(body, dict):
            body = {}
    except ValueError:  # connector should send JSON; stay defensive.
        body = {}
    logger.info(
        f"[VoxPro] transfer-result (transfer_id={transfer_id}): {json.dumps(body)}"
    )

    manager = await get_call_transfer_manager()

    # Authenticate BEFORE claiming the step: an unauthenticated caller must not
    # be able to burn the one-shot claim and lock out the genuine callback.
    context = await manager.get_transfer_context(transfer_id)
    if context is None:
        logger.warning(
            f"[VoxPro] no transfer context for {transfer_id}; rejecting callback"
        )
        raise HTTPException(status_code=404, detail="Unknown transfer")

    if not await _verify_transfer_result_signature(context, request, raw_body, body):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Connector callbacks may be retried; only the first delivery publishes.
    if not await manager.claim_transfer_step(transfer_id, "transfer-result"):
        logger.info(f"[VoxPro] duplicate transfer-result for {transfer_id}; ignoring")
        return {"status": "duplicate"}

    event = build_transfer_event(transfer_id, context, body)
    await manager.publish_transfer_event(event)
    logger.info(f"[VoxPro] published {event.type} for transfer {transfer_id}")
    return {"status": "ok", "event": event.type.value}
