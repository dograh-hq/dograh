"""Resolve retry eligibility before a campaign call becomes terminal."""

from datetime import UTC, datetime, timedelta

from loguru import logger

from api.db import db_client
from api.db.models import WorkflowRunModel


async def schedule_campaign_retry(
    workflow_run: WorkflowRunModel, reason: str, *, organization_id: int
) -> None:
    if not workflow_run.campaign_id:
        return
    # Legacy campaign runs may have no queue link and cannot be retried.
    if workflow_run.queued_run_id is None:
        return
    campaign = await db_client.get_campaign(
        workflow_run.campaign_id, organization_id=organization_id
    )
    if campaign is None or campaign.state not in ("running", "syncing", "paused"):
        return
    config = campaign.retry_config or {}
    if not config.get("enabled", True) or not config.get(f"retry_on_{reason}", True):
        return
    queued_run = await db_client.get_queued_run_by_id(workflow_run.queued_run_id)
    if queued_run is None or queued_run.campaign_id != campaign.id:
        raise ValueError(f"Campaign run {workflow_run.id} has no matching queue row")

    scheduled_for = None
    if queued_run.retry_count < config.get("max_retries", 1):
        scheduled_for = datetime.now(UTC) + timedelta(
            seconds=config.get("retry_delay_seconds", 120)
        )
    retry_id = await db_client.record_campaign_retry_decision(
        workflow_run_id=workflow_run.id,
        campaign_id=campaign.id,
        organization_id=organization_id,
        reason=reason,
        scheduled_for=scheduled_for,
    )
    logger.info(
        f"campaign_id: {campaign.id} - Retry decision recorded for run "
        f"{workflow_run.id}: reason={reason}, retry_run_id={retry_id}"
    )
