"""Scheduled sweep that heals stuck VoiceLink client provisioning.

Orgs whose ``create_client`` failed (e.g. the reseller had no active plan or no
available channels) are left ``voicelink_status = "pending"``. This cron retries
them idempotently so that once the reseller account is recharged (active plan +
channels) every pending client provisions automatically — no manual retries.
"""

from loguru import logger

from api.db import db_client
from api.services.voicelink_clients.service import (
    VOICELINK_STATUS_PENDING,
    VOICELINK_STATUS_PROVISIONED,
    ensure_voicelink_client,
)


async def retry_pending_voicelink_provisioning(ctx) -> dict:
    """Retry VoiceLink client creation for every org stuck in ``pending``."""
    organizations = await db_client.list_organizations_with_users()
    pending = [
        org
        for org in organizations
        if org.voicelink_status == VOICELINK_STATUS_PENDING
        and not org.voicelink_client_id
    ]
    if not pending:
        return {"pending": 0, "provisioned": 0}

    provisioned = 0
    for org in pending:
        result = await ensure_voicelink_client(org.id)
        if result.get("status") == VOICELINK_STATUS_PROVISIONED:
            provisioned += 1

    logger.info(
        f"VoiceLink retry sweep: {len(pending)} pending, {provisioned} newly "
        f"provisioned"
    )
    return {"pending": len(pending), "provisioned": provisioned}
