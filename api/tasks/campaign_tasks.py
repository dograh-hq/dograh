from datetime import UTC, datetime
from typing import Dict

from loguru import logger

from api.db import db_client
from api.enums import RedisChannel
from api.services.campaign.call_dispatcher import campaign_call_dispatcher
from api.services.campaign.campaign_event_protocol import BatchFailedEvent
from api.services.campaign.campaign_event_publisher import (
    get_campaign_event_publisher,
)
from api.services.campaign.source_sync_factory import get_sync_service


async def sync_campaign_source(ctx: Dict, campaign_id: int) -> None:
    """
    Phase 1: Syncs data from configured source to queued_runs table
    - Campaign state should already be 'syncing'
    - Determines source type from campaign configuration
    - Fetches data via appropriate sync service (Google Sheets, HubSpot, etc.)
    - Creates queued_run entries with unique source_uuid
    - Updates campaign total_rows
    - Transitions campaign state to 'running' on success
    - Enqueues process_campaign_batch tasks
    """
    logger.info(f"Starting source sync for campaign {campaign_id}")

    try:
        # Get campaign
        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        # Get appropriate sync service
        sync_service = get_sync_service(campaign.source_type)

        # Sync source data
        rows_synced = await sync_service.sync_source_data(campaign_id)

        if rows_synced == 0:
            # No data to process
            await db_client.update_campaign(
                campaign_id=campaign_id,
                state="completed",
                completed_at=datetime.now(UTC),
                source_sync_status="completed",
                source_last_synced_at=datetime.now(UTC),
            )
            logger.info(f"Campaign {campaign_id} completed with no data to process")
            return

        # Update campaign state to running
        await db_client.update_campaign(
            campaign_id=campaign_id,
            state="running",
            source_sync_status="completed",
            source_last_synced_at=datetime.now(UTC),
        )

        # Publish sync completed event - orchestrator will schedule first batch
        publisher = await get_campaign_event_publisher()
        await publisher.publish_sync_completed(
            campaign_id=campaign_id,
            total_rows=rows_synced,
            source_type=campaign.source_type,
            source_id=campaign.source_id,
        )

        logger.info(
            f"Campaign {campaign_id} source sync completed, {rows_synced} rows synced"
        )

    except Exception as e:
        logger.error(f"Error syncing campaign {campaign_id} source: {e}")

        # Update campaign with error
        await db_client.update_campaign(
            campaign_id=campaign_id,
            state="failed",
            source_sync_status="failed",
            source_sync_error=str(e),
        )
        raise


async def process_campaign_batch(
    ctx: Dict, campaign_id: int, batch_size: int = 10
) -> None:
    """
    Phase 2: Processes a batch of queued runs
    - Fetches next batch of 'queued' runs (including due retries)
    - Creates workflow runs with context variables
    - Initiates Twilio calls with rate limiting
    - Updates queued_run state to 'processed'
    - Updates campaign.processed_rows counter
    - Publishes batch_completed event for orchestrator
    """
    logger.info(f"Processing batch for campaign {campaign_id}, batch_size={batch_size}")

    failed_count = 0
    try:
        # Process the batch
        processed_count = await campaign_call_dispatcher.process_batch(
            campaign_id=campaign_id, batch_size=batch_size
        )

        # Publish batch completed event - orchestrator will handle next batch scheduling
        publisher = await get_campaign_event_publisher()
        await publisher.publish_batch_completed(
            campaign_id=campaign_id,
            processed_count=processed_count,
            failed_count=failed_count,
            batch_size=batch_size,
        )

        logger.info(
            f"Campaign {campaign_id} batch completed: processed={processed_count}, "
            f"failed={failed_count}"
        )

    except Exception as e:
        logger.error(f"Error processing batch for campaign {campaign_id}: {e}")

        # Publish batch failed event
        publisher = await get_campaign_event_publisher()
        event = BatchFailedEvent(
            campaign_id=campaign_id,
            error=str(e),
            processed_count=0,
        )
        await publisher.redis.publish(
            RedisChannel.CAMPAIGN_EVENTS.value, event.to_json()
        )

        # Update campaign state to failed
        await db_client.update_campaign(campaign_id=campaign_id, state="failed")
        raise


async def monitor_campaign_progress(ctx: Dict, campaign_id: int) -> None:
    """
    Phase 3: Monitors campaign completion
    - Checks if all queued runs are in 'processed' state
    - Queries workflow_runs for final call statistics
    - Updates campaign state to 'completed'
    - Calculates total calls made, successful, failed
    - Triggers post-campaign integrations
    """
    logger.info(f"Monitoring progress for campaign {campaign_id}")

    try:
        # Get campaign
        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        # Check if all runs are processed
        pending_runs = await db_client.count_queued_runs(
            campaign_id=campaign_id, state="queued"
        )

        if pending_runs > 0:
            logger.info(f"Campaign {campaign_id} still has {pending_runs} pending runs")
            return

        # All runs processed, mark campaign as completed
        await db_client.update_campaign(
            campaign_id=campaign_id, state="completed", completed_at=datetime.now(UTC)
        )

        # Calculate statistics
        workflow_runs = await db_client.get_workflow_runs_by_campaign(campaign_id)

        total_calls = len(workflow_runs)
        successful_calls = 0
        failed_calls = 0

        for run in workflow_runs:
            callbacks = run.logs.get("telephony_status_callbacks", [])
            if callbacks:
                final_status = callbacks[-1].get("status", "").lower()
                if final_status == "completed":
                    successful_calls += 1
                elif final_status in ["failed", "busy", "no-answer"]:
                    failed_calls += 1

        logger.info(
            f"Campaign {campaign_id} completed: "
            f"Total calls: {total_calls}, "
            f"Successful: {successful_calls}, "
            f"Failed: {failed_calls}"
        )

        # TODO: Trigger post-campaign integrations if configured

    except Exception as e:
        logger.error(f"Error monitoring campaign {campaign_id}: {e}")
        raise
