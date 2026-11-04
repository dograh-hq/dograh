"""Persist the significant events of a Cloudonix call transfer on its run.

Once the caller is handed to the conference the pipeline has ended, so
nothing else records whether the caller actually joined, which leg left
first, or how each leg ended. These entries land in
``workflow_runs.logs["transfer_events"]``.

Cloudonix posts a session callback on every session update (QoS stats,
profile writes, ...), most of them repeating the previous status, and
delivers them concurrently and out of order. Only status changes, conference
joins/leaves and Dograh's own switch request are kept, and nothing is
recorded for a leg after its terminal status.
"""

from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.db import db_client
from api.services.telephony.call_transfer_manager import get_call_transfer_manager
from api.services.telephony.providers.cloudonix.provider import (
    CLOUDONIX_TERMINAL_SESSION_STATUSES,
    session_diagnostics,
)

TRANSFER_EVENTS_LOG_KEY = "transfer_events"

# "new" and "processing" precede dialing and say nothing about the call.
_RECORDED_SESSION_STATUSES = CLOUDONIX_TERMINAL_SESSION_STATUSES | {
    "ringing",
    "connected",
}


def _should_append(entry: dict[str, Any]):
    transfer_id = entry["transfer_id"]
    leg = entry["leg"]
    source = entry["source"]

    def check(existing: list[dict]) -> bool:
        same_leg = [
            e
            for e in existing
            if e.get("transfer_id") == transfer_id and e.get("leg") == leg
        ]
        if source == "conference":
            return not any(
                e.get("source") == "conference"
                and e.get("event") == entry["event"]
                and e.get("session") == entry.get("session")
                for e in same_leg
            )
        if source == "session":
            statuses = [e["event"] for e in same_leg if e.get("source") == "session"]
            if any(s in CLOUDONIX_TERMINAL_SESSION_STATUSES for s in statuses):
                return False
            return not statuses or statuses[-1] != entry["event"]
        return True

    return check


async def record_transfer_event(
    transfer_id: str, leg: str, source: str, event: str, **details: Any
) -> None:
    """Append one event to the run the transfer belongs to.

    Never raises: this is diagnostics, and its callers are driving a live
    transfer.
    """
    try:
        manager = await get_call_transfer_manager()
        run_id = await manager.get_transfer_run_id(transfer_id)
        if run_id is None:
            logger.debug(
                f"[Cloudonix Transfer] No run for {transfer_id}; not recording {event}"
            )
            return
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "transfer_id": transfer_id,
            "leg": leg,
            "source": source,
            "event": event,
            **{key: value for key, value in details.items() if value not in (None, "")},
        }
        await db_client.append_workflow_run_log_entry(
            run_id,
            TRANSFER_EVENTS_LOG_KEY,
            entry,
            should_append=_should_append(entry),
        )
    except Exception as e:
        logger.warning(f"[Cloudonix Transfer] Failed to record {event}: {e}")


async def record_transfer_callback(
    transfer_id: str, leg: str, data: dict[str, Any]
) -> None:
    """Record a Cloudonix session or conference callback for a transfer leg."""
    conference_event = str(data.get("StatusCallbackEvent") or "").lower()
    if conference_event:
        await record_transfer_event(
            transfer_id,
            leg,
            "conference",
            conference_event,
            session=data.get("Session"),
            call_sid=data.get("CallSid"),
            participant_status=data.get("ParticipantCallStatus"),
            reason=data.get("ReasonParticipantLeft"),
        )
        return

    status = str(data.get("status") or "").lower()
    if status in _RECORDED_SESSION_STATUSES:
        await record_transfer_event(
            transfer_id, leg, "session", status, **session_diagnostics(data)
        )
