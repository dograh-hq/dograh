"""WhatsApp call-permission orchestration for campaigns.

Meta gates business-initiated calls on the recipient's consent, so a campaign
lead can be parked waiting for it and has to be woken when it arrives. That is
campaign scheduling work - queued-run state transitions, campaign counters,
batch enqueues - driven by a provider-specific signal, and it is kept out of
``service.py``, which owns call transport and the lifecycle of a live call.
The two share only the Meta client and Redis handles imported below.

Entry points:
  * ``reactivate_campaign_runs_for_recipient`` - a grant or denial arrived
    (webhook, or a poll below) for one recipient.
  * ``sync_whatsapp_permissions_for_campaign`` - ask Meta about every recipient
    this campaign has parked.
  * ``sync_all_parked_whatsapp_permissions`` - the cron sweep over campaigns.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc

from typing import Dict, Optional

from loguru import logger

from api.db import db_client
from api.services.telephony.providers.whatsapp.config import (
    GRANTED_PERMISSION_STATUSES,
    is_granted_permission_status,
    is_revoked_permission_status,
    normalize_whatsapp_permission_status,
    parse_whatsapp_expiration,
)
from api.services.telephony.providers.whatsapp.service import (
    get_or_create_whatsapp_client as _get_or_create_whatsapp_client,
)
from api.services.telephony.providers.whatsapp.service import (
    get_whatsapp_redis as _get_redis,
)


async def reactivate_campaign_runs_for_recipient(
    phone_number: str,
    status: str,
    phone_number_id: Optional[str] = None,
    telephony_configuration_id: Optional[int] = None,
    campaign_id: Optional[int] = None,
) -> int:
    """Reactivate parked campaign runs when a recipient grants call permission, or fail them if denied."""
    if not phone_number:
        return 0

    clean_phone = phone_number.strip().lstrip("+")
    try:
        from api.tasks.arq import enqueue_job
        from api.tasks.function_names import FunctionNames

        # Resolve target configuration id if phone_number_id is supplied.
        #
        # get_queued_runs_awaiting_whatsapp_permission searches parked runs
        # across every organization - the recipient number is the only key it
        # has - so target_config_id is the ONLY tenant boundary on the filter
        # below. Letting an unresolved id fall through to "no filter" would let
        # one business's grant or denial activate or fail another business's
        # campaign run for the same recipient. Resolution can legitimately fail:
        # the configuration may have been deactivated since the webhook was
        # verified, or the id may resolve ambiguously (that lookup refuses to
        # guess). Refuse rather than run unscoped.
        target_config_id = telephony_configuration_id
        if target_config_id is None and phone_number_id:
            cfg = await db_client.get_whatsapp_configuration_by_phone_number_id(phone_number_id)
            if not cfg:
                logger.error(
                    f"[WhatsApp] Cannot resolve a telephony configuration for "
                    f"phone_number_id={phone_number_id!r}; refusing to touch parked "
                    f"runs for {clean_phone} without a tenant boundary."
                )
                return 0
            target_config_id = cfg.id

        if target_config_id is None and campaign_id is None:
            logger.error(
                f"[WhatsApp] Refusing to reactivate parked runs for {clean_phone}: "
                "neither a telephony configuration nor a campaign scopes this request."
            )
            return 0

        waiting_runs = await db_client.get_queued_runs_awaiting_whatsapp_permission(clean_phone)
        if not waiting_runs:
            waiting_runs = await db_client.get_queued_runs_awaiting_whatsapp_permission("+" + clean_phone)

        if not waiting_runs:
            return 0

        # Parked runs for one recipient cluster into a handful of campaigns,
        # but the tenant check below runs per run and the enqueue pass needs
        # the same rows again. Read each campaign once per call instead: the
        # cron sweep walks every parked recipient, so a read per run turned a
        # single sweep into hundreds of sequential queries.
        campaigns_seen: Dict[int, Optional[object]] = {}

        async def _campaign(camp_id: Optional[int]):
            if camp_id is None:
                return None
            if camp_id not in campaigns_seen:
                campaigns_seen[camp_id] = await db_client.get_campaign_by_id(camp_id)
            return campaigns_seen[camp_id]

        # Filter waiting runs by campaign_id or telephony_configuration_id if provided
        filtered_runs = []
        for q_run in waiting_runs:
            if campaign_id is not None and q_run.campaign_id != campaign_id:
                continue
            if target_config_id is not None:
                camp = await _campaign(q_run.campaign_id)
                if not camp or camp.telephony_configuration_id != target_config_id:
                    continue
            filtered_runs.append(q_run)

        if not filtered_runs:
            return 0

        if is_granted_permission_status(status):
            logger.info(
                f"[WhatsApp] Found {len(filtered_runs)} queued run(s) awaiting permission for {clean_phone}. "
                "Activating for immediate dialing."
            )
            # activate_queued_run_for_immediate_dial is a conditional claim: it
            # returns None, changing nothing, when the run was no longer parked
            # on this permission. The same grant reaches us more than once -
            # Meta redelivers the permission webhook, the messages webhook can
            # carry it too, and the cron sync polls for it - so ignoring that
            # result would have every loser report the same runs as reactivated
            # and enqueue its own batch for a campaign it did not wake.
            triggered_campaign_ids = set()
            activated = 0
            for q_run in filtered_runs:
                claimed = await db_client.activate_queued_run_for_immediate_dial(
                    q_run.id
                )
                if claimed is None:
                    logger.debug(
                        f"[WhatsApp] Queued run {q_run.id} was no longer parked on "
                        f"permission for {clean_phone}; another grant path claimed it"
                    )
                    continue
                activated += 1
                triggered_campaign_ids.add(q_run.campaign_id)

            if not activated:
                return 0

            # The activations above are already durable and are what actually
            # unparks these leads; the enqueue only brings the dial forward
            # from the campaign orchestrator's next poll to now. So one
            # campaign's enqueue failing must not discard the other campaigns'
            # or make this report zero reactivated runs when it activated some
            # - the recipient would then look permanently parked to the caller
            # while the orchestrator quietly dialled them anyway.
            for camp_id in triggered_campaign_ids:
                try:
                    campaign = await _campaign(camp_id)
                    if campaign and campaign.state == "running":
                        await enqueue_job(
                            FunctionNames.PROCESS_CAMPAIGN_BATCH,
                            camp_id,
                            10,
                        )
                        logger.info(
                            f"[WhatsApp] Enqueued immediate batch for campaign {camp_id} "
                            f"after recipient {clean_phone} granted call permission."
                        )
                except Exception as e:
                    logger.warning(
                        f"[WhatsApp] Could not enqueue an immediate batch for campaign "
                        f"{camp_id} after {clean_phone} granted permission: {e}. "
                        "Its runs are activated; the orchestrator's next batch dials them."
                    )
            return activated

        elif is_revoked_permission_status(status):
            # Same conditional-claim contract as the grant path above: only the
            # writer that found the run still parked moved it, and only that
            # one may report it.
            failed = 0
            denied_campaign_ids = set()
            for q_run in filtered_runs:
                claimed = await db_client.fail_queued_run_permission_denied(q_run.id)
                if claimed is None:
                    continue
                failed += 1
                if q_run.campaign_id:
                    denied_campaign_ids.add(q_run.campaign_id)
                logger.info(
                    f"[WhatsApp] Marked queued run {q_run.id} as failed (permission_denied) "
                    f"for recipient {clean_phone}."
                )

            # Once per campaign, after its denials are committed: the recount
            # locks the campaign row, and one recipient can hold several parked
            # leads in the same campaign. Failing here only leaves progress
            # stale until the next recompute - the queued-run state it is
            # derived from is already durable - so it must not undo a denial
            # that has landed.
            for camp_id in denied_campaign_ids:
                try:
                    await db_client.sync_campaign_processed_rows(camp_id)
                except Exception as e:
                    logger.warning(
                        f"[WhatsApp] Failed to refresh processed_rows for campaign "
                        f"{camp_id} after denying {clean_phone}: {e}"
                    )
            return failed
    except Exception as e:
        logger.warning(
            f"[WhatsApp] Error reactivating queued runs on permission change for {clean_phone}: {e}"
        )
    return 0


@dataclass(frozen=True)
class WhatsAppPermissionSyncResult:
    """Outcome of a permission sync.

    ``throttled`` distinguishes "the cooldown skipped this run" from "we asked
    Meta and nobody had granted permission". Both reactivate zero runs, and a
    caller that only sees the count reports the second when it means the first.
    """

    reactivated: int = 0
    throttled: bool = False


async def sync_whatsapp_permissions_for_campaign(
    campaign_id: int, force: bool = False
) -> WhatsAppPermissionSyncResult:
    """
    Syncs WhatsApp call permission status from Meta Graph API for any leads parked
    in awaiting_whatsapp_permission for this campaign.

    If permission has been granted, activates the queued run for immediate dialing.
    If force is False, enforces a 30s cooldown per campaign via Redis to avoid
    spamming Meta - each sync costs one Graph call per parked recipient.

    The cooldown lives here and only here. Claiming it is the check: SET NX is
    atomic, so two concurrent callers cannot both decide they are first, which a
    separate read-then-write could. Callers must not reconstruct the key.
    """
    if not campaign_id:
        return WhatsAppPermissionSyncResult()

    try:
        parked_runs = await db_client.get_all_queued_runs_awaiting_whatsapp_permission(
            campaign_id=campaign_id
        )
        if not parked_runs:
            return WhatsAppPermissionSyncResult()

        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign or campaign.state != "running":
            return WhatsAppPermissionSyncResult()

        if not campaign.telephony_configuration_id:
            return WhatsAppPermissionSyncResult()

        config = await db_client.get_telephony_configuration(
            campaign.telephony_configuration_id
        )
        if not config or config.provider != "whatsapp":
            return WhatsAppPermissionSyncResult()

        creds = config.credentials or {}
        phone_number_id = creds.get("phone_number_id")
        access_token = creds.get("access_token")
        if not phone_number_id or not access_token:
            return WhatsAppPermissionSyncResult()

        recipients = set()
        for r in parked_runs:
            pn = str((r.context_variables or {}).get("phone_number") or "").strip()
            if pn:
                recipients.add(pn)

        if not recipients:
            return WhatsAppPermissionSyncResult()

        client = _get_or_create_whatsapp_client(
            phone_number_id=phone_number_id,
            access_token=access_token,
            app_secret=creds.get("app_secret"),
        )

        redis_client = await _get_redis()
        cooldown_key = f"wa_perm_sync_cooldown:{campaign_id}"
        if not force and redis_client:
            try:
                claimed = await redis_client.set(cooldown_key, "1", nx=True, ex=30)
                if not claimed:
                    return WhatsAppPermissionSyncResult(throttled=True)
            except Exception:
                pass
        elif force and redis_client:
            try:
                await redis_client.set(cooldown_key, "1", ex=30)
            except Exception:
                pass

        reactivated_total = 0
        now = datetime.now(UTC)

        for recipient in recipients:
            clean_recipient = recipient.lstrip("+")
            try:
                meta_res = await client.check_call_permission(clean_recipient)
                if not meta_res or "error" in meta_res:
                    continue

                meta_perm = meta_res.get("permission") or {}
                meta_status = meta_perm.get("status") or meta_res.get("status")
                can_start_call = False
                for act in meta_res.get("actions") or []:
                    if act.get("action_name") == "start_call" and act.get("can_perform_action", False):
                        can_start_call = True

                normalized_status = normalize_whatsapp_permission_status(meta_status)

                if can_start_call or normalized_status in GRANTED_PERMISSION_STATUSES:
                    actual_status = (
                        normalized_status
                        if normalized_status in GRANTED_PERMISSION_STATUSES
                        else "granted_temporary"
                    )
                    perm_type = (
                        "permanent" if actual_status == "granted_permanent" else "temporary"
                    )
                    # Persist Meta's expiry: a temporary grant stored without one
                    # would later read as a permission that never lapses.
                    meta_expires_at = parse_whatsapp_expiration(
                        meta_perm.get("expiration_time") or meta_res.get("expiration")
                    )
                    await db_client.upsert_whatsapp_call_permission(
                        organization_id=config.organization_id,
                        telephony_configuration_id=config.id,
                        phone_number_id=phone_number_id,
                        recipient_phone_number=recipient,
                        status=actual_status,
                        permission_type=perm_type,
                        expires_at=meta_expires_at,
                        granted_at=now,
                    )
                    count = await reactivate_campaign_runs_for_recipient(
                        recipient,
                        actual_status,
                        phone_number_id=phone_number_id,
                        telephony_configuration_id=config.id,
                        campaign_id=campaign_id,
                    )
                    reactivated_total += count
                elif is_revoked_permission_status(normalized_status):
                    await db_client.upsert_whatsapp_call_permission(
                        organization_id=config.organization_id,
                        telephony_configuration_id=config.id,
                        phone_number_id=phone_number_id,
                        recipient_phone_number=recipient,
                        status=normalized_status,
                        expires_at=None,
                    )
                    await reactivate_campaign_runs_for_recipient(
                        recipient,
                        normalized_status,
                        phone_number_id=phone_number_id,
                        telephony_configuration_id=config.id,
                        campaign_id=campaign_id,
                    )
            except Exception as e:
                logger.warning(
                    f"[WhatsApp] Failed checking call permission for {recipient} in campaign {campaign_id}: {e}"
                )

        return WhatsAppPermissionSyncResult(reactivated=reactivated_total)
    except Exception as e:
        logger.warning(f"[WhatsApp] Error in sync_whatsapp_permissions_for_campaign: {e}")
        return WhatsAppPermissionSyncResult()


async def sync_all_parked_whatsapp_permissions() -> int:
    """Check and sync WhatsApp call permissions across all running campaigns with parked runs."""
    try:
        parked = await db_client.get_all_queued_runs_awaiting_whatsapp_permission()
        if not parked:
            return 0
        campaign_ids = {r.campaign_id for r in parked}
        total = 0
        for cid in campaign_ids:
            total += (
                await sync_whatsapp_permissions_for_campaign(cid, force=False)
            ).reactivated
        return total
    except Exception as e:
        logger.warning(f"[WhatsApp] Error in sync_all_parked_whatsapp_permissions: {e}")
        return 0
