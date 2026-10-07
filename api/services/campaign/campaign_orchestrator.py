"""Campaign Orchestrator Service.

This service ensures continuous campaign processing by listening to events
and scheduling batches immediately upon completion. It also monitors campaigns
for completion once source work and calls are terminal, and handles retry events.
"""

from api.logging_config import setup_logging

setup_logging()


import asyncio
import signal
from datetime import UTC, datetime, timedelta
from typing import Dict
from zoneinfo import ZoneInfo

import redis.asyncio as aioredis
from loguru import logger

from api.constants import CAMPAIGN_PROCESSING_CLAIM_TIMEOUT_SECONDS, REDIS_URL
from api.db import db_client
from api.db.models import CampaignModel
from api.enums import RedisChannel
from api.services.campaign.campaign_call_dispatcher import campaign_call_dispatcher
from api.services.campaign.campaign_event_protocol import (
    BatchCompletedEvent,
    BatchFailedEvent,
    CallCompletedEvent,
    CircuitBreakerTrippedEvent,
    RetryNeededEvent,
    SyncCompletedEvent,
    parse_campaign_event,
)
from api.services.campaign.campaign_event_publisher import CampaignEventPublisher
from api.services.campaign.campaign_retry import schedule_campaign_retry
from api.services.campaign.circuit_breaker import circuit_breaker
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames


class CampaignOrchestrator:
    """Orchestrates campaign processing, retry handling, and completion detection."""

    def __init__(self, redis_client: aioredis.Redis):
        self.redis = redis_client
        self.publisher = CampaignEventPublisher(redis_client)
        self.completion_check_interval = 60  # 1 minute
        self._processing_locks: Dict[int, datetime] = {}  # prevent duplicate scheduling
        self._batch_in_progress: Dict[
            int, datetime
        ] = {}  # track batches that have been scheduled but not completed
        self._running = False
        self._pubsub = None

    async def run(self):
        """Main service with two concurrent tasks."""
        self._running = True
        logger.info("Campaign Orchestrator starting...")

        try:
            # Task 1: Listen for events and react immediately
            event_task = asyncio.create_task(self._listen_for_events())

            # Task 2: Periodically check for stale campaigns
            completion_task = asyncio.create_task(self._monitor_completion())

            # Wait for both tasks
            await asyncio.gather(event_task, completion_task)

        except asyncio.CancelledError:
            logger.info("Campaign Orchestrator cancelled")
            raise
        except Exception as e:
            logger.error(f"Campaign Orchestrator error: {e}")
            raise
        finally:
            await self.shutdown()

    async def _listen_for_events(self):
        """Listen for campaign events and react immediately."""
        self._pubsub = self.redis.pubsub()
        await self._pubsub.subscribe(RedisChannel.CAMPAIGN_EVENTS.value)
        logger.info(f"Subscribed to {RedisChannel.CAMPAIGN_EVENTS.value} channel")

        async for message in self._pubsub.listen():
            if not self._running:
                break

            if message["type"] == "message":
                try:
                    event = parse_campaign_event(message["data"])
                    if event:
                        await self._handle_event(event)
                    else:
                        logger.error(
                            f"Failed to parse campaign event: {message['data']}"
                        )
                except Exception as e:
                    logger.error(f"Error handling campaign event: {e}")

    async def _handle_event(self, event):
        """Handle campaign events including retry events."""
        # All events should have campaign_id
        if not hasattr(event, "campaign_id") or not event.campaign_id:
            logger.warning(f"Event missing campaign_id: {type(event).__name__}")
            return

        campaign_id = event.campaign_id

        logger.debug(
            f"campaign_id: {campaign_id} - Received event: {type(event).__name__}"
        )

        if isinstance(event, RetryNeededEvent):
            await self._handle_retry_event(event)

        elif isinstance(event, BatchCompletedEvent):
            # Clear the batch in progress flag
            if campaign_id in self._batch_in_progress:
                del self._batch_in_progress[campaign_id]
                logger.debug(
                    f"campaign_id: {campaign_id} - Batch completed, cleared in-progress flag"
                )

            # Check campaign state before scheduling next batch
            campaign = await db_client.get_campaign_by_id(campaign_id)
            if not campaign:
                logger.error(f"campaign_id: {campaign_id} - Campaign not found")
                self._clear_campaign_state(campaign_id)
                return

            if campaign.state != "running":
                logger.info(
                    f"campaign_id: {campaign_id} - Campaign not in running state ({campaign.state}), "
                    f"not scheduling next batch"
                )
                self._clear_campaign_state(campaign_id)
                return

            # Immediately schedule next batch, or complete if all calls have ended.
            await self._schedule_next_batch(campaign_id)
            await self._complete_campaign(campaign)

        elif isinstance(event, CallCompletedEvent):
            campaign = await db_client.get_campaign_by_id(campaign_id)
            if campaign is not None and campaign.state == "running":
                await self._schedule_next_batch(campaign_id)
                await self._complete_campaign(campaign)

        elif isinstance(event, BatchFailedEvent):
            # Clear the batch in progress flag
            if campaign_id in self._batch_in_progress:
                del self._batch_in_progress[campaign_id]

            logger.warning(
                f"campaign_id: {campaign_id} - Batch failed: {event.error}, "
                f"scheduling next batch to continue processing"
            )

            # Lets not schedule another batch, since we mark the campaign
            # as failed just to be on the safe side from process_campaign_batch
            # if a batch fails

        elif isinstance(event, SyncCompletedEvent):
            # Start processing after sync
            logger.info(
                f"campaign_id: {campaign_id} - Sync completed, starting processing"
            )
            await self._schedule_next_batch(campaign_id)
            campaign = await db_client.get_campaign_by_id(campaign_id)
            if campaign is not None:
                await self._complete_campaign(campaign)

        elif isinstance(event, CircuitBreakerTrippedEvent):
            # Circuit breaker tripped - clear state for this campaign
            logger.warning(
                f"campaign_id: {campaign_id} - Circuit breaker tripped event received: "
                f"failure_rate={event.failure_rate:.2%}"
            )
            self._clear_campaign_state(campaign_id)

    async def _handle_retry_event(self, event: RetryNeededEvent):
        """Accept legacy retry events through the same idempotent decision path."""
        campaign = await db_client.get_campaign_by_id(event.campaign_id)
        if campaign is None:
            return
        run = await db_client.get_workflow_run(
            event.workflow_run_id, organization_id=campaign.organization_id
        )
        if (
            run is None
            or run.campaign_id != campaign.id
            or run.queued_run_id != event.queued_run_id
        ):
            logger.warning(
                f"campaign_id: {campaign.id} - Ignoring mismatched retry event"
            )
            return
        await schedule_campaign_retry(
            run, event.reason, organization_id=campaign.organization_id
        )
        await self._schedule_next_batch(campaign.id)
        await self._complete_campaign(campaign)

    def _is_within_schedule(self, campaign: CampaignModel) -> bool:
        """Check if the current time falls within the campaign's schedule windows.

        Returns True (allow scheduling) if:
        - No schedule_config in metadata
        - Schedule is disabled
        - No slots configured
        - Invalid timezone (fail open)
        - Current time matches a slot
        """
        if not campaign.orchestrator_metadata:
            return True

        schedule_config = campaign.orchestrator_metadata.get("schedule_config")
        if not schedule_config:
            return True

        if not schedule_config.get("enabled", False):
            return True

        slots = schedule_config.get("slots")
        if not slots:
            return True

        timezone_str = schedule_config.get("timezone", "UTC")
        try:
            tz = ZoneInfo(timezone_str)
        except (KeyError, Exception):
            logger.warning(
                f"campaign_id: {campaign.id} - Invalid timezone '{timezone_str}' in schedule_config, "
                f"failing open (allowing scheduling)"
            )
            return True

        now = datetime.now(tz)
        current_day = now.weekday()  # 0=Monday through 6=Sunday
        current_time = now.strftime("%H:%M")

        for slot in slots:
            if slot.get("day_of_week") == current_day:
                start = slot.get("start_time", "")
                end = slot.get("end_time", "")
                if start <= current_time < end:
                    return True

        return False

    async def _schedule_next_batch(self, campaign_id: int):
        """Schedule next batch immediately if work available."""

        if campaign_id in self._batch_in_progress:
            return

        # Prevent duplicate scheduling with in-memory lock
        if campaign_id in self._processing_locks:
            lock_time = self._processing_locks[campaign_id]
            if (datetime.now(UTC) - lock_time).total_seconds() < 5:
                logger.debug(
                    f"campaign_id: {campaign_id} - Batch already scheduled recently"
                )
                return

        # Set lock
        self._processing_locks[campaign_id] = datetime.now(UTC)

        try:
            # Check campaign status
            campaign = await db_client.get_campaign_by_id(campaign_id)
            if not campaign:
                logger.error(f"campaign_id: {campaign_id} - Campaign not found")
                return

            if campaign.state not in ["running", "syncing"]:
                logger.info(
                    f"campaign_id: {campaign_id} - Campaign not in running state: {campaign.state}"
                )
                return

            # Check schedule window before scheduling
            if not self._is_within_schedule(campaign):
                logger.info(
                    f"campaign_id: {campaign_id} - Outside scheduled time window, skipping batch"
                )
                return

            # Safety net: check circuit breaker before scheduling
            cb_config = None
            if campaign.orchestrator_metadata:
                cb_config = campaign.orchestrator_metadata.get("circuit_breaker")

            is_open, stats = await circuit_breaker.is_circuit_open(
                campaign_id=campaign_id,
                config=cb_config,
            )

            if is_open and stats:
                logger.warning(
                    f"campaign_id: {campaign_id} - Circuit breaker is open, "
                    f"pausing campaign. Stats: {stats}"
                )
                await db_client.update_campaign(campaign_id=campaign_id, state="paused")
                await db_client.append_campaign_log(
                    campaign_id=campaign_id,
                    level="warning",
                    event="circuit_breaker_tripped",
                    message=(
                        f"Paused at scheduling: failure rate "
                        f"{stats['failure_rate']:.2%} "
                        f"({stats['failure_count']}/"
                        f"{stats['failure_count'] + stats['success_count']}) "
                        f"exceeded threshold {stats['threshold']:.2%} "
                        f"in {stats['window_seconds']}s window"
                    ),
                    details=stats,
                )
                await self.publisher.publish_circuit_breaker_tripped(
                    campaign_id=campaign_id,
                    failure_rate=stats["failure_rate"],
                    failure_count=stats["failure_count"],
                    success_count=stats["success_count"],
                    threshold=stats["threshold"],
                    window_seconds=stats["window_seconds"],
                )
                self._clear_campaign_state(campaign_id)
                return

            # Check for available work (queued runs + due retries)
            has_work = await db_client.has_dispatchable_campaign_runs(
                campaign_id, campaign.organization_id
            )

            if has_work:
                # Schedule batch immediately
                self._batch_in_progress[campaign_id] = datetime.now(UTC)
                await enqueue_job(
                    FunctionNames.PROCESS_CAMPAIGN_BATCH,
                    campaign_id,
                    20,  # batch_size; setup parallelism is bounded by the dispatcher
                )
                logger.info(f"campaign_id: {campaign_id} - Scheduled next batch")

                # Set batch in progress flag
                self._batch_in_progress[campaign_id] = datetime.now(UTC)

                # Update database
                await db_client.update_campaign(
                    campaign_id=campaign_id,
                    last_batch_scheduled_at=datetime.now(UTC),
                    last_activity_at=datetime.now(UTC),
                )
            else:
                logger.info(
                    f"campaign_id: {campaign_id} - No pending work to process, "
                    f"campaign may complete or wait for retries"
                )

        except Exception as e:
            self._batch_in_progress.pop(campaign_id, None)
            logger.error(f"campaign_id: {campaign_id} - Error scheduling batch: {e}")
        finally:
            # The in-progress flag prevents duplicate batches. A time-based
            # lock would discard completion events for batches finishing in <5s.
            self._processing_locks.pop(campaign_id, None)

    def _clear_campaign_state(self, campaign_id: int):
        """Clear all in-memory state for a campaign."""
        if campaign_id in self._processing_locks:
            del self._processing_locks[campaign_id]
        if campaign_id in self._batch_in_progress:
            del self._batch_in_progress[campaign_id]
        logger.debug(f"campaign_id: {campaign_id} - Cleared all in-memory state")

    async def _monitor_completion(self):
        """Periodically check for campaigns that should be marked complete."""
        while self._running:
            try:
                await self._check_stale_campaigns()
            except Exception as e:
                logger.error(f"Completion monitoring failed: {e}")

            await asyncio.sleep(self.completion_check_interval)

    async def _check_stale_campaigns(self):
        """Check all running campaigns for completion or orphaned work."""
        logger.debug("Checking for stale campaigns...")

        await campaign_call_dispatcher.recover_stale_dispatches()
        campaigns = await db_client.get_campaigns_by_status(statuses=["running"])

        for campaign in campaigns:
            try:
                campaign_id = campaign.id

                # A killed worker cannot return its persisted claims in finally.
                # Recover them before looking for dispatchable work or completion,
                # even after an orchestrator restart has lost the batch flags.
                recovered = await db_client.recover_stale_campaign_claims(
                    campaign_id,
                    campaign.organization_id,
                    claimed_before=datetime.now(UTC)
                    - timedelta(seconds=CAMPAIGN_PROCESSING_CLAIM_TIMEOUT_SECONDS),
                )
                if recovered:
                    logger.warning(
                        f"campaign_id: {campaign_id} - Recovered {recovered} expired claims"
                    )

                # Check if batch is stuck (initiated > 5 minutes ago but no completion)
                if campaign_id in self._batch_in_progress:
                    batch_start_time = self._batch_in_progress[campaign_id]
                    time_since_batch_start = (
                        datetime.now(UTC) - batch_start_time
                    ).total_seconds()

                    if time_since_batch_start > 300:  # 5 minutes
                        logger.warning(
                            f"campaign_id: {campaign_id} - Batch stuck for {time_since_batch_start:.0f}s, "
                            f"clearing flag and checking for more work"
                        )
                        del self._batch_in_progress[campaign_id]

                        # Check if there's work to be done
                        if await db_client.has_dispatchable_campaign_runs(
                            campaign_id, campaign.organization_id
                        ):
                            logger.info(
                                f"campaign_id: {campaign_id} - Found pending work after stuck batch, "
                                f"scheduling new batch"
                            )
                            await self._schedule_next_batch(campaign_id)
                            continue

                # Check for orphaned work (e.g., newly created retries with no batch in progress)
                if campaign_id not in self._batch_in_progress:
                    has_work = await db_client.has_dispatchable_campaign_runs(
                        campaign_id, campaign.organization_id
                    )
                    if has_work:
                        if not self._is_within_schedule(campaign):
                            logger.info(
                                f"campaign_id: {campaign_id} - Found orphaned work but outside "
                                f"schedule window, skipping"
                            )
                            continue
                        logger.info(
                            f"campaign_id: {campaign_id} - Found orphaned work (likely new retries), "
                            f"scheduling batch to process"
                        )
                        await self._schedule_next_batch(campaign_id)
                        continue

                # Check if campaign should be marked complete
                await self._complete_campaign(campaign)
            except Exception as e:
                logger.error(
                    f"campaign_id: {campaign.id} - Completion check failed: {e}"
                )

    async def _complete_campaign(self, campaign: CampaignModel):
        """Mark campaign as complete."""
        campaign_id = campaign.id

        if campaign_id in self._batch_in_progress:
            return

        try:
            campaign = await db_client.complete_campaign_if_idle(
                campaign_id, campaign.organization_id
            )
            if campaign is None:
                return

            logger.info(f"campaign_id: {campaign_id} - Campaign marked as completed")

            # Calculate duration if started_at is available
            duration = None
            if campaign.started_at:
                duration = (datetime.now(UTC) - campaign.started_at).total_seconds()

            # Publish completion event
            await self.publisher.publish_campaign_completed(
                campaign_id=campaign_id,
                total_rows=campaign.total_rows or 0,
                processed_rows=campaign.processed_rows,
                failed_rows=campaign.failed_rows,
                duration_seconds=duration,
            )

            # Clean up in-memory state
            self._clear_campaign_state(campaign_id)

        except Exception as e:
            logger.error(
                f"campaign_id: {campaign_id} - Failed to complete campaign: {e}"
            )

    async def shutdown(self):
        """Clean shutdown of the orchestrator."""
        logger.info("Campaign Orchestrator shutting down...")
        self._running = False

        if self._pubsub:
            try:
                await self._pubsub.unsubscribe(RedisChannel.CAMPAIGN_EVENTS.value)
                await self._pubsub.aclose()
            except Exception as e:
                logger.error(f"Error closing pubsub: {e}")

        logger.info("Campaign Orchestrator shutdown complete")


async def main():
    """Main entry point for Campaign Orchestrator service."""

    # Setup Redis connection
    redis = await aioredis.from_url(REDIS_URL, decode_responses=True)

    # Create and run orchestrator
    orchestrator = CampaignOrchestrator(redis)

    # Create a shutdown event for clean coordination
    shutdown_event = asyncio.Event()

    # Setup signal handlers
    loop = asyncio.get_event_loop()

    def signal_handler(signum):
        logger.info(f"Received shutdown signal {signum}")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))

    # Run orchestrator with shutdown monitoring
    orchestrator_task = asyncio.create_task(orchestrator.run())
    shutdown_task = asyncio.create_task(shutdown_event.wait())

    try:
        # Wait for either orchestrator to complete or shutdown signal
        done, _ = await asyncio.wait(
            [orchestrator_task, shutdown_task], return_when=asyncio.FIRST_COMPLETED
        )

        # If shutdown was triggered, stop the orchestrator
        if shutdown_task in done:
            logger.info("Shutdown signal received, stopping orchestrator...")
            orchestrator._running = False
            # Cancel the orchestrator task immediately since it may be blocked
            orchestrator_task.cancel()
            try:
                await orchestrator_task
            except asyncio.CancelledError:
                logger.info("Orchestrator task cancelled successfully")

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        # Ensure clean shutdown
        await orchestrator.shutdown()
        await redis.aclose()

        logger.info("Campaign Orchestrator service stopped")


if __name__ == "__main__":
    asyncio.run(main())
