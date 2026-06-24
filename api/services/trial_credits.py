"""Trial minute ledger gate for outbound calling.

New orgs are granted DEFAULT_FREE_CALL_SECONDS of outbound call time; a NULL
balance means UNLIMITED (existing/paid/owner orgs). These helpers centralize the
allow / decrement decision used at campaign start/resume, per dispatch batch, the
public trigger, and the post-call decrement.
"""

from __future__ import annotations

from typing import Optional, Union

from fastapi import HTTPException
from loguru import logger

from api.db import db_client

TRIAL_EXHAUSTED_MESSAGE = (
    "Your free trial minutes are used up. Add credits to keep making calls."
)


async def has_free_call_seconds(organization_id: int) -> bool:
    """True if the org may dial (unmetered, or balance > 0)."""
    remaining = await db_client.get_free_call_seconds_remaining(organization_id)
    return remaining is None or remaining > 0


async def assert_has_free_call_seconds(organization_id: int) -> None:
    """Raise HTTP 402 when the org's trial balance is exhausted; no-op otherwise."""
    if not await has_free_call_seconds(organization_id):
        raise HTTPException(status_code=402, detail=TRIAL_EXHAUSTED_MESSAGE)


async def consume_free_call_seconds(
    organization_id: int, seconds: Optional[Union[int, float]]
) -> None:
    """Decrement the trial balance by a completed call's duration (best-effort).

    No-op for unmetered orgs / missing duration. Never raises into the caller —
    a ledger hiccup must not break post-call processing.
    """
    if not seconds or seconds <= 0:
        return
    try:
        await db_client.decrement_free_call_seconds(
            organization_id, int(round(seconds))
        )
    except Exception as exc:
        logger.warning(
            f"Trial-credit decrement failed for org {organization_id}: {exc}"
        )
