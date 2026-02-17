"""Campaign event publisher for orchestrator communication.

Handles publishing of campaign events to Redis pub/sub channels.
"""

from typing import Dict, Optional

import redis.asyncio as aioredis
from loguru import logger

from api.constants import REDIS_URL
from api.enums import RedisChannel
from api.services.campaign.campaign_event_protocol import (
    BatchCompletedEvent,
    BatchFailedEvent,
    CampaignCompletedEvent,
    CircuitBreakerTrippedEvent,
    RetryNeededEvent,
    SyncCompletedEvent,
)


class CampaignEventPublisher:
    """Helper class for publishing campaign events."""

    def __init__(self, redis_client):
        self.redis = redis_client

    async def publish_batch_completed(
        self,
        campaign_id: int,
        processed_count: int,
        failed_count: int = 0,
        batch_size: int = 0,
        metadata: Optional[Dict] = None,
    ):
        """Publish batch completed event."""
        event = BatchCompletedEvent(
            campaign_id=campaign_id,
            processed_count=processed_count,
            failed_count=failed_count,
            batch_size=batch_size,
            metadata=metadata,
        )

        await self.redis.publish(RedisChannel.CAMPAIGN_EVENTS.value, event.to_json())

    async def publish_batch_failed(
        self,
        campaign_id: int,
        error: str,
        processed_count: int = 0,
        metadata: Optional[Dict] = None,
    ):
        """Publish batch failed event."""
        event = BatchFailedEvent(
            campaign_id=campaign_id,
            error=error,
            processed_count=processed_count,
            metadata=metadata,
        )

        await self.redis.publish(RedisChannel.CAMPAIGN_EVENTS.value, event.to_json())

    async def publish_sync_completed(
        self,
        campaign_id: int,
        total_rows: int,
        source_type: str = "",
        source_id: str = "",
        metadata: Optional[Dict] = None,
    ):
        """Publish sync completed event."""
        event = SyncCompletedEvent(
            campaign_id=campaign_id,
            total_rows=total_rows,
            source_type=source_type,
            source_id=source_id,
            metadata=metadata,
        )

        await self.redis.publish(RedisChannel.CAMPAIGN_EVENTS.value, event.to_json())

    async def publish_retry_needed(
        self,
        workflow_run_id: int,
        reason: str,
        campaign_id: Optional[int] = None,
        queued_run_id: Optional[int] = None,
        metadata: Optional[Dict] = None,
    ):
        """Publish retry needed event."""
        event = RetryNeededEvent(
            campaign_id=campaign_id or 0,
            workflow_run_id=workflow_run_id,
            queued_run_id=queued_run_id or 0,
            reason=reason,
            metadata=metadata or {},
        )

        await self.redis.publish(RedisChannel.CAMPAIGN_EVENTS.value, event.to_json())

        logger.info(
            f"Published retry event for workflow_run {workflow_run_id}, "
            f"reason: {reason}, campaign: {campaign_id}"
        )

    async def publish_campaign_completed(
        self,
        campaign_id: int,
        total_rows: int,
        processed_rows: int,
        failed_rows: int,
        duration_seconds: Optional[float] = None,
    ):
        """Publish campaign completed event."""
        event = CampaignCompletedEvent(
            campaign_id=campaign_id,
            total_rows=total_rows,
            processed_rows=processed_rows,
            failed_rows=failed_rows,
            duration_seconds=duration_seconds,
        )

        await self.redis.publish(RedisChannel.CAMPAIGN_EVENTS.value, event.to_json())

    async def publish_circuit_breaker_tripped(
        self,
        campaign_id: int,
        failure_rate: float,
        failure_count: int,
        success_count: int,
        threshold: float,
        window_seconds: int,
    ):
        """Publish circuit breaker tripped event."""
        event = CircuitBreakerTrippedEvent(
            campaign_id=campaign_id,
            failure_rate=failure_rate,
            failure_count=failure_count,
            success_count=success_count,
            threshold=threshold,
            window_seconds=window_seconds,
        )

        await self.redis.publish(RedisChannel.CAMPAIGN_EVENTS.value, event.to_json())

        logger.warning(
            f"Published circuit breaker tripped event for campaign {campaign_id}: "
            f"failure_rate={failure_rate:.2%} ({failure_count} failures)"
        )


# Global publisher instance with lazy Redis connection
async def get_campaign_event_publisher() -> CampaignEventPublisher:
    """Get or create the campaign event publisher."""
    global _campaign_publisher
    global _campaign_redis_client

    if "_campaign_publisher" not in globals():
        _campaign_redis_client = await aioredis.from_url(
            REDIS_URL, decode_responses=True
        )
        _campaign_publisher = CampaignEventPublisher(_campaign_redis_client)

    return _campaign_publisher
