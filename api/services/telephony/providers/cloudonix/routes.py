"""Cloudonix telephony routes (webhooks, status callbacks, answer URLs).

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via the
provider registry — see ProviderSpec.router.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.db import db_client
from api.enums import TelephonyCallStatus
from api.services.telephony.call_transfer_manager import get_call_transfer_manager
from api.services.telephony.factory import get_telephony_provider_for_run
from api.services.telephony.providers.cloudonix.provider import CloudonixProvider
from api.services.telephony.providers.cloudonix.transfer_log import (
    record_transfer_callback,
)
from api.services.telephony.status_processor import (
    TERMINAL_NOT_CONNECTED_STATUSES,
    StatusCallbackRequest,
    _process_status_update,
)
from api.services.telephony.transfer_event_protocol import (
    TransferEvent,
    TransferEventType,
)

router = APIRouter()

# Cloudonix session statuses that terminate a transfer without an answer.
_CLOUDONIX_TRANSFER_FAILURE_STATUSES = {
    "busy",
    "noanswer",
    "cancel",
    "nocredit",
    "error",
    "congestion",
    "failed",
}


async def _read_callback_body(request: Request) -> dict[str, Any]:
    """The callback body as a dict, or ``{}`` when it cannot be parsed."""
    try:
        if "application/json" in request.headers.get("content-type", ""):
            data = await request.json()
        else:
            data = dict(await request.form())
    except Exception as e:
        logger.warning(f"[Cloudonix] Unparseable callback body: {e}")
        return {}
    # Cloudonix session notifications may arrive as a single object or a
    # one-element list (see session-update webhook payloads).
    if isinstance(data, list):
        data = data[0] if data else {}
    return data if isinstance(data, dict) else {}


@router.post("/cloudonix/transfer-result/{transfer_id}")
async def handle_cloudonix_transfer_result(transfer_id: str, request: Request):
    """Drive transfer completion from the destination leg's callbacks.

    ``CloudonixProvider.transfer_call`` sets this URL as the destination
    call's session ``callback`` and as its conference ``statusCallback``. The
    destination joining the conference publishes DESTINATION_ANSWERED so the
    shared handler forks the caller in; a terminal non-answer session status
    publishes TRANSFER_FAILED. Everything else is acked without publishing.

    Significant events are also recorded on the run, including those that
    arrive after the transfer context is gone (the destination leaving the
    conference, its session ending).
    """
    data = await _read_callback_body(request)
    result = await _drive_transfer(transfer_id, data)
    await record_transfer_callback(transfer_id, "destination", data)
    return result


@router.post("/cloudonix/transfer-result/{transfer_id}/caller")
async def handle_cloudonix_transfer_caller_event(transfer_id: str, request: Request):
    """Record the caller leg's conference join/leave events.

    A 200 on the caller's application switch only means Cloudonix accepted
    it; the caller's own participant-join is the evidence that it reached the
    conference. These arrive on their own URL so a caller join can never be
    read as the destination answering.
    """
    data = await _read_callback_body(request)
    logger.info(
        f"[Cloudonix Transfer] transfer_id={transfer_id} leg=caller "
        f"conferenceStatus={str(data.get('StatusCallbackEvent', '')).lower()} "
        f"token={data.get('Session', '')}"
    )
    await record_transfer_callback(transfer_id, "caller", data)
    return {"status": "recorded"}


async def _drive_transfer(transfer_id: str, data: dict[str, Any]) -> dict[str, str]:
    conferenceStatus = str(data.get("StatusCallbackEvent", "")).lower()
    outboundCallStatus = str(data.get("status", "")).lower()
    destination_token = data.get("Session", "")

    logger.info(
        f"[Cloudonix Transfer] transfer_id={transfer_id} status={outboundCallStatus} "
        f"conferenceStatus={conferenceStatus} token={destination_token}"
    )

    call_transfer_manager = await get_call_transfer_manager()
    transfer_context = await call_transfer_manager.get_transfer_context(transfer_id)
    if not transfer_context:
        # Normal once the caller has been switched: the context is cleaned up
        # then, and later callbacks are only recorded.
        logger.info(
            f"[Cloudonix Transfer] No transfer context for {transfer_id}; "
            "not driving the transfer"
        )
        return {"status": "ignored", "reason": "unknown_transfer"}

    original_call_sid = transfer_context.original_call_sid
    conference_name = transfer_context.conference_name

    if conferenceStatus == "participant-join":
        event = TransferEvent(
            type=TransferEventType.DESTINATION_ANSWERED,
            transfer_id=transfer_id,
            original_call_sid=original_call_sid or "",
            transfer_call_sid=destination_token,
            conference_name=conference_name,
            message="Great! The destination answered. Connecting you now.",
            status="success",
            action="destination_answered",
        )
    elif outboundCallStatus in _CLOUDONIX_TRANSFER_FAILURE_STATUSES:
        event = TransferEvent(
            type=TransferEventType.TRANSFER_FAILED,
            transfer_id=transfer_id,
            original_call_sid=original_call_sid or "",
            transfer_call_sid=destination_token,
            conference_name=conference_name,
            message="The transfer call could not be completed.",
            status="transfer_failed",
            action="transfer_failed",
            reason=outboundCallStatus,
        )
    else:
        logger.info(
            f"[Cloudonix Transfer] Intermediate status {outboundCallStatus} for {transfer_id}, "
            "waiting"
        )
        return {"status": "pending"}

    await call_transfer_manager.publish_transfer_event(event)
    return {"status": "completed"}


# Once one of these is recorded the run's callback log is closed: a late,
# out-of-order "connected" must not become the last entry, which campaign
# failure counting reads.
_FINAL_STATUS_VALUES = frozenset(
    status.value
    for status in TERMINAL_NOT_CONNECTED_STATUSES | {TelephonyCallStatus.COMPLETED}
)


def _is_status_change(recorded: list[dict], status: TelephonyCallStatus | str) -> bool:
    """Whether a session callback carries news for the run's callback log.

    Cloudonix posts on every session update (QoS stats, profile writes, ...),
    most of them repeating the previous status, and delivers them out of order.
    """
    known = TelephonyCallStatus.from_raw(status)
    value = known.value if known else str(status).lower()
    if any(entry.get("status") in _FINAL_STATUS_VALUES for entry in recorded):
        return False
    return not recorded or recorded[-1].get("status") != value


async def _cloudonix_provider_for_run(workflow_run) -> CloudonixProvider | None:
    if not workflow_run:
        return None
    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        return None
    try:
        provider = await get_telephony_provider_for_run(
            workflow_run, workflow.organization_id
        )
    except Exception as e:
        logger.warning(f"[run {workflow_run.id}] No telephony provider: {e}")
        return None
    return provider if isinstance(provider, CloudonixProvider) else None


@router.post("/cloudonix/status-callback/{workflow_run_id}/{token}")
async def handle_cloudonix_status_callback(
    workflow_run_id: int,
    token: str,
    request: Request,
):
    """Session callbacks for outbound calls placed by ``initiate_call``.

    ``token`` is an HMAC over the run id keyed on the domain's API key, so
    only URLs Dograh minted are accepted. An unknown run and a bad token get
    the same 404, so run ids cannot be probed. Status changes go to the shared
    status processor, which ends a busy / noanswer / failed call exactly as it
    does for every other provider.
    """
    set_current_run_id(workflow_run_id)

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    provider = await _cloudonix_provider_for_run(workflow_run)
    if not provider or not provider.verify_status_callback_token(
        workflow_run_id, token
    ):
        logger.warning(
            f"[run {workflow_run_id}] Cloudonix status callback rejected: "
            "unknown run, non-Cloudonix run or invalid token"
        )
        raise HTTPException(status_code=404, detail="Workflow run not found")

    parsed = provider.parse_status_callback(await _read_callback_body(request))
    raw_status = parsed["extra"]["cloudonix_status"]
    logger.info(
        f"[run {workflow_run_id}] Cloudonix status callback: "
        f"session={parsed['call_id']} status={raw_status}"
    )
    if not raw_status:
        return {"status": "ignored", "reason": "no_status"}

    expected_call_id = (workflow_run.gathered_context or {}).get("call_id")
    if expected_call_id and parsed["call_id"] != expected_call_id:
        logger.warning(
            f"[run {workflow_run_id}] Cloudonix status callback for session "
            f"{parsed['call_id']!r}, run is on {expected_call_id!r}; ignoring"
        )
        return {"status": "ignored", "reason": "session_mismatch"}

    recorded = (workflow_run.logs or {}).get("telephony_status_callbacks") or []
    if not _is_status_change(recorded, parsed["status"]):
        return {"status": "ignored", "reason": "unchanged"}

    await _process_status_update(
        workflow_run_id,
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


@router.post("/cloudonix/cdr")
async def handle_cloudonix_cdr(request: Request):
    """Handle Cloudonix CDR (Call Detail Record) webhooks.

    Cloudonix sends CDR records when calls complete. The CDR contains:
    - domain: Used to identify the organization
    - call_id: Used to find the workflow run
    - disposition: Call termination status (ANSWER, BUSY, CANCEL, FAILED, CONGESTION, NOANSWER)
    - duration/billsec: Call duration information
    """
    try:
        cdr_data = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse Cloudonix CDR JSON: {e}")
        return {"status": "error", "message": "Invalid JSON payload"}

    # Extract domain to find organization
    domain = cdr_data.get("domain")
    if not domain:
        logger.warning("Cloudonix CDR missing domain field")
        return {"status": "error", "message": "Missing domain field"}

    # Extract call_id to find workflow run
    session = cdr_data.get("session")
    call_id = session.get("token") if isinstance(session, dict) else None
    logger.info(f"Cloudonix CDR data for call id {call_id} - {cdr_data}")
    if not call_id:
        logger.warning("Cloudonix CDR missing call_id field")
        return {"status": "error", "message": "Missing call_id field"}

    # Find workflow run by call_id in gathered_context
    workflow_run = await db_client.get_workflow_run_by_call_id(call_id)
    if not workflow_run:
        logger.warning(f"No workflow run found for Cloudonix call_id: {call_id}")
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    workflow_run_id = workflow_run.id
    set_current_run_id(workflow_run_id)
    logger.info(f"[run {workflow_run_id}] Processing Cloudonix CDR for call {call_id}")

    parsed_data = CloudonixProvider.parse_cdr_status_callback(cdr_data)
    status_update = StatusCallbackRequest(
        call_id=parsed_data["call_id"],
        status=parsed_data["status"],
        from_number=parsed_data.get("from_number"),
        to_number=parsed_data.get("to_number"),
        duration=parsed_data.get("duration"),
        extra=parsed_data.get("extra", {}),
    )

    # Process the status update
    await _process_status_update(workflow_run_id, status_update)

    logger.info(
        f"[run {workflow_run_id}] Cloudonix CDR processed successfully - "
        f"disposition: {cdr_data.get('disposition')}, status: {status_update.status}"
    )

    return {"status": "success"}
