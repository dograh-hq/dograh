"""Race-safe call-credit reservation + reconcile (single local ledger).

reserve a fixed hold before a run (atomic, so concurrent calls can't oversell),
then reconcile on completion: release the hold and charge the true duration so
the net deduction equals the call's actual length. Reconcile degrades to a plain
post-call charge when no reservation was taken, so it is safe at every entry point.
"""

from __future__ import annotations

from loguru import logger

from api.db import db_client
from api.services.trial_credits import consume_free_call_seconds

RESERVED_CREDIT_SECONDS_KEY = "reserved_credit_seconds"
# Set once settlement has run so a retried post-call task can't double-charge.
CREDITS_SETTLED_KEY = "credits_settled"

INSUFFICIENT_CREDITS_MESSAGE = (
    "You're out of calling credits. Add credits from Billing to keep making calls."
)


async def reserve_call_credits(organization_id: int, est_seconds: int) -> int | None:
    """Reserve `est_seconds` of credits for an in-flight call.

    Returns the reserved seconds (0 when the org is unmetered/unlimited, i.e. no
    charge) on success, or None when the metered balance cannot cover the estimate.
    """
    balance = await db_client.get_free_call_seconds_remaining(organization_id)
    if balance is None:
        return 0  # unmetered / unlimited — allowed, nothing reserved
    if est_seconds <= 0:
        return 0
    if await db_client.try_charge_call_seconds(organization_id, est_seconds):
        return est_seconds
    return None


async def reconcile_call_credits(
    organization_id: int, reserved_seconds: int, actual_seconds: float | int | None
) -> None:
    """Release the reservation hold, then charge the true call duration.

    Net deduction == actual usage. No-op for unmetered orgs (consume skips NULL).
    Best-effort: a ledger hiccup must never break post-call processing.
    """
    try:
        if reserved_seconds and reserved_seconds > 0:
            balance = await db_client.get_free_call_seconds_remaining(organization_id)
            if balance is not None:  # never convert an unmetered org to metered
                await db_client.add_call_seconds(organization_id, int(reserved_seconds))
        await consume_free_call_seconds(organization_id, actual_seconds)
    except Exception as exc:
        logger.warning(f"Credit reconcile failed for org {organization_id}: {exc}")


async def settle_workflow_run_credits(organization_id: int, workflow_run) -> None:
    """Reconcile credits for a completed run from its reserved hold + duration.

    Idempotent: the ARQ post-call task can be retried, so we guard on a
    ``credits_settled`` flag stored in ``initial_context`` (merged, never
    overwritten) and no-op if this run was already settled — a retry must never
    release the hold or charge the duration twice.
    """
    ctx = getattr(workflow_run, "initial_context", None) or {}
    if ctx.get(CREDITS_SETTLED_KEY):
        return
    reserved = ctx.get(RESERVED_CREDIT_SECONDS_KEY) or 0
    usage = getattr(workflow_run, "usage_info", None) or {}
    cost = getattr(workflow_run, "cost_info", None) or {}
    duration = usage.get("call_duration_seconds") or cost.get("call_duration_seconds")
    await reconcile_call_credits(organization_id, reserved, duration)

    run_id = getattr(workflow_run, "id", None)
    if run_id is not None:
        # Merge-write the flag so the reserved-seconds key is preserved.
        await db_client.update_workflow_run(
            run_id, initial_context={CREDITS_SETTLED_KEY: True}
        )
