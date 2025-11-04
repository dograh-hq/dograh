from datetime import UTC, datetime
from typing import Any, Dict

from loguru import logger

from api.db import db_client
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames


class CampaignRunnerService:
    """Orchestrates campaign execution"""

    async def start_campaign(self, campaign_id: int) -> None:
        """Entry point - updates state to 'syncing' and enqueues sync task"""
        # Get campaign
        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        if campaign.state != "created":
            raise ValueError(
                f"Campaign must be in 'created' state to start, current state: {campaign.state}"
            )

        # Update campaign state to syncing
        await db_client.update_campaign(
            campaign_id=campaign_id,
            state="syncing",
            started_at=datetime.now(UTC),
            source_sync_status="in_progress",
        )

        # Enqueue the sync task
        await enqueue_job(FunctionNames.SYNC_CAMPAIGN_SOURCE, campaign_id)

        logger.info(f"Campaign {campaign_id} started, syncing source data")

    async def pause_campaign(self, campaign_id: int) -> None:
        """Pauses active campaign processing"""
        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        if campaign.state not in ["running", "syncing"]:
            raise ValueError(
                f"Campaign must be in 'running' or 'syncing' state to pause, current state: {campaign.state}"
            )

        # Update state to paused
        await db_client.update_campaign(campaign_id=campaign_id, state="paused")

        logger.info(f"Campaign {campaign_id} paused")

    async def resume_campaign(self, campaign_id: int) -> None:
        """Resumes paused campaign"""
        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        if campaign.state != "paused":
            raise ValueError(
                f"Campaign must be in 'paused' state to resume, current state: {campaign.state}"
            )

        # Update state to running
        await db_client.update_campaign(campaign_id=campaign_id, state="running")

        # Enqueue process batch task to continue processing
        await enqueue_job(FunctionNames.PROCESS_CAMPAIGN_BATCH, campaign_id)

        logger.info(f"Campaign {campaign_id} resumed")

    async def get_campaign_status(self, campaign_id: int) -> Dict[str, Any]:
        """Returns detailed campaign status"""
        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        # Count failed calls from workflow runs
        failed_calls = await self._count_failed_campaign_calls(campaign_id)

        return {
            "campaign_id": campaign_id,
            "state": campaign.state,
            "total_rows": campaign.total_rows or 0,
            "processed_rows": campaign.processed_rows,
            "failed_calls": failed_calls,
            "progress_percentage": (
                (campaign.processed_rows / campaign.total_rows * 100)
                if campaign.total_rows and campaign.total_rows > 0
                else 0
            ),
            "source_sync": {
                "status": campaign.source_sync_status,
                "last_synced_at": campaign.source_last_synced_at,
                "error": campaign.source_sync_error,
            },
            "rate_limit": campaign.rate_limit_per_second,
            "started_at": campaign.started_at,
            "completed_at": campaign.completed_at,
        }

    async def _count_failed_campaign_calls(self, campaign_id: int) -> int:
        """Count failed calls by examining workflow_run telephony callbacks"""
        # Get all workflow runs for this campaign
        workflow_runs = await db_client.get_workflow_runs_by_campaign(campaign_id)

        failed_count = 0
        for run in workflow_runs:
            callbacks = run.logs.get("telephony_status_callbacks", [])
            if callbacks:
                # Check final status
                final_status = callbacks[-1].get("status", "").lower()
                if final_status in ["failed", "busy", "no-answer"]:
                    failed_count += 1

        return failed_count


# Global instance
campaign_runner_service = CampaignRunnerService()
