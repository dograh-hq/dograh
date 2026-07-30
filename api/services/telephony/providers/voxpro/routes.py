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

from fastapi import APIRouter, Request
from loguru import logger

from api.services.telephony.call_transfer_manager import get_call_transfer_manager
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


@router.post("/voxpro/transfer-result/{transfer_id}")
async def handle_voxpro_transfer_result(transfer_id: str, request: Request):
    """Publish the completion event for a blind transfer.

    ``transfer_id`` is an unguessable UUID minted per transfer by the shared flow
    and only known to the connector we handed it to via ``result_url`` — the same
    trust model as Telnyx's transfer webhook_url (no separate signature).
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — connector should send JSON; stay defensive.
        body = {}
    logger.info(
        f"[VoxPro] transfer-result (transfer_id={transfer_id}): {json.dumps(body)}"
    )

    manager = await get_call_transfer_manager()

    # Connector callbacks may be retried; only the first delivery publishes.
    if not await manager.claim_transfer_step(transfer_id, "transfer-result"):
        logger.info(f"[VoxPro] duplicate transfer-result for {transfer_id}; ignoring")
        return {"status": "duplicate"}

    context = await manager.get_transfer_context(transfer_id)
    if context is None:
        # Wait may have already timed out and cleaned up; nothing to unblock.
        logger.warning(
            f"[VoxPro] no transfer context for {transfer_id} — "
            "publishing event anyway in case the wait is still active"
        )

    event = build_transfer_event(transfer_id, context, body)
    await manager.publish_transfer_event(event)
    logger.info(f"[VoxPro] published {event.type} for transfer {transfer_id}")
    return {"status": "ok", "event": event.type.value}
