"""Campaign circuit breaker for automatic pause on high failure rates.

Uses two Redis sorted sets (ZSETs) per campaign — one for failures, one for
successes — as sliding windows.  ZCARD gives O(1) counts without iterating
members, keeping the Lua scripts simple.

A separate capped Redis list (``cb_recent_failures:{campaign_id}``) stores the
last N failing ``{workflow_run_id, reason, ts}`` entries so the campaign log
written when the breaker trips can show *which* calls pushed it over.
"""

import json
import time
from typing import Any, Dict, List, Optional, Tuple

import redis.asyncio as aioredis
from loguru import logger

from api.constants import DEFAULT_CIRCUIT_BREAKER_CONFIG, REDIS_URL
from api.db import db_client
from api.services.campaign.campaign_event_publisher import get_campaign_event_publisher

# Cap on the number of recent failure entries kept per campaign — large enough
# to be useful for debugging a trip, small enough that the JSON details stay
# bounded.
MAX_RECENT_FAILURES = 20


class CircuitBreaker:
    """Sliding window circuit breaker for campaign call failures."""

    def __init__(self):
        self.redis_client: Optional[aioredis.Redis] = None

    async def _get_redis(self) -> aioredis.Redis:
        """Get or create Redis connection."""
        if self.redis_client is None:
            self.redis_client = await aioredis.from_url(
                REDIS_URL, decode_responses=True
            )
        return self.redis_client

    @staticmethod
    def _keys(campaign_id: int) -> Tuple[str, str]:
        """Return (failures_key, successes_key) for a campaign."""
        return f"cb_failures:{campaign_id}", f"cb_successes:{campaign_id}"

    @staticmethod
    def _recent_failures_key(campaign_id: int) -> str:
        """Return the Redis key used for the capped recent-failures list."""
        return f"cb_recent_failures:{campaign_id}"

    async def _push_recent_failure(
        self,
        campaign_id: int,
        workflow_run_id: int,
        reason: Optional[str],
    ) -> None:
        """Push a failure entry onto the capped recent-failures list."""
        redis_client = await self._get_redis()
        key = self._recent_failures_key(campaign_id)
        entry = json.dumps(
            {
                "workflow_run_id": workflow_run_id,
                "reason": reason,
                "ts": time.time(),
            }
        )
        try:
            await redis_client.lpush(key, entry)
            await redis_client.ltrim(key, 0, MAX_RECENT_FAILURES - 1)
            # Keep this list around as long as the sliding window plus a buffer.
            await redis_client.expire(
                key,
                DEFAULT_CIRCUIT_BREAKER_CONFIG["window_seconds"] + 60,
            )
        except Exception as e:
            # Never let recent-failure bookkeeping disrupt the call path.
            logger.error(
                f"Failed to record recent failure for campaign {campaign_id}: {e}"
            )

    async def _get_recent_failures(self, campaign_id: int) -> List[Dict[str, Any]]:
        """Return the recent-failures list (most-recent first)."""
        redis_client = await self._get_redis()
        key = self._recent_failures_key(campaign_id)
        try:
            entries = await redis_client.lrange(key, 0, -1)
        except Exception as e:
            logger.error(
                f"Failed to read recent failures for campaign {campaign_id}: {e}"
            )
            return []
        decoded: List[Dict[str, Any]] = []
        for raw in entries:
            try:
                decoded.append(json.loads(raw))
            except (TypeError, ValueError):
                continue
        return decoded

    async def record_call_outcome(
        self,
        campaign_id: int,
        is_failure: bool,
        config: Optional[dict] = None,
    ) -> Tuple[bool, Optional[dict]]:
        """Record a call outcome and check if the circuit breaker should trip.

        Args:
            campaign_id: The campaign ID.
            is_failure: True if the call failed, False if succeeded.
            config: Optional per-campaign circuit breaker config override.
                    Falls back to DEFAULT_CIRCUIT_BREAKER_CONFIG.

        Returns:
            Tuple of (tripped: bool, stats: dict or None).
            If tripped is True, stats contains failure_rate, failure_count,
            success_count, threshold, window_seconds.
        """
        cb_config = {**DEFAULT_CIRCUIT_BREAKER_CONFIG, **(config or {})}

        if not cb_config.get("enabled", True):
            return False, None

        redis_client = await self._get_redis()

        window_seconds = cb_config["window_seconds"]
        threshold = cb_config["failure_threshold"]
        min_calls = cb_config["min_calls_in_window"]

        now = time.time()
        window_start = now - window_seconds

        fail_key, succ_key = self._keys(campaign_id)

        lua_script = """
        local fail_key = KEYS[1]
        local succ_key = KEYS[2]
        local now = tonumber(ARGV[1])
        local window_start = tonumber(ARGV[2])
        local is_failure = tonumber(ARGV[3])
        local threshold = tonumber(ARGV[4])
        local min_calls = tonumber(ARGV[5])
        local ttl = tonumber(ARGV[6])

        -- Trim both sets to the sliding window
        redis.call('ZREMRANGEBYSCORE', fail_key, 0, window_start)
        redis.call('ZREMRANGEBYSCORE', succ_key, 0, window_start)

        -- Add the new outcome to the appropriate set
        if is_failure == 1 then
            redis.call('ZADD', fail_key, now, now)
        else
            redis.call('ZADD', succ_key, now, now)
        end

        -- Refresh TTL on both keys
        redis.call('EXPIRE', fail_key, ttl)
        redis.call('EXPIRE', succ_key, ttl)

        -- Count via ZCARD (O(1))
        local failures = redis.call('ZCARD', fail_key)
        local successes = redis.call('ZCARD', succ_key)
        local total = failures + successes

        -- Check trip condition
        if total >= min_calls and (failures / total) >= threshold then
            return {1, failures, successes, total}
        end

        return {0, failures, successes, total}
        """

        try:
            result = await redis_client.eval(
                lua_script,
                2,
                fail_key,
                succ_key,
                now,
                window_start,
                1 if is_failure else 0,
                threshold,
                min_calls,
                window_seconds + 60,  # TTL with buffer
            )

            tripped = bool(result[0])
            failure_count = int(result[1])
            success_count = int(result[2])
            total = int(result[3])
            failure_rate = failure_count / total if total > 0 else 0.0

            if tripped:
                logger.warning(
                    f"Circuit breaker TRIPPED for campaign {campaign_id}: "
                    f"failure_rate={failure_rate:.2%} ({failure_count}/{total}) "
                    f"threshold={threshold:.2%} window={window_seconds}s"
                )

            stats = {
                "failure_rate": failure_rate,
                "failure_count": failure_count,
                "success_count": success_count,
                "threshold": threshold,
                "window_seconds": window_seconds,
            }
            return tripped, stats

        except Exception as e:
            logger.error(f"Circuit breaker error for campaign {campaign_id}: {e}")
            # Fail open - do NOT trip on errors
            return False, None

    async def is_circuit_open(
        self,
        campaign_id: int,
        config: Optional[dict] = None,
    ) -> Tuple[bool, Optional[dict]]:
        """Check if the circuit breaker is in open (tripped) state without recording.

        Used as a safety net check before scheduling batches.
        """
        cb_config = {**DEFAULT_CIRCUIT_BREAKER_CONFIG, **(config or {})}

        if not cb_config.get("enabled", True):
            return False, None

        redis_client = await self._get_redis()

        window_seconds = cb_config["window_seconds"]
        threshold = cb_config["failure_threshold"]
        min_calls = cb_config["min_calls_in_window"]

        now = time.time()
        window_start = now - window_seconds

        fail_key, succ_key = self._keys(campaign_id)

        lua_script = """
        local fail_key = KEYS[1]
        local succ_key = KEYS[2]
        local window_start = tonumber(ARGV[1])
        local threshold = tonumber(ARGV[2])
        local min_calls = tonumber(ARGV[3])

        -- Trim both sets
        redis.call('ZREMRANGEBYSCORE', fail_key, 0, window_start)
        redis.call('ZREMRANGEBYSCORE', succ_key, 0, window_start)

        -- Count via ZCARD
        local failures = redis.call('ZCARD', fail_key)
        local successes = redis.call('ZCARD', succ_key)
        local total = failures + successes

        if total >= min_calls and (failures / total) >= threshold then
            return {1, failures, successes, total}
        end

        return {0, failures, successes, total}
        """

        try:
            result = await redis_client.eval(
                lua_script,
                2,
                fail_key,
                succ_key,
                window_start,
                threshold,
                min_calls,
            )

            is_open = bool(result[0])
            failure_count = int(result[1])
            success_count = int(result[2])
            total = int(result[3])
            failure_rate = failure_count / total if total > 0 else 0.0

            stats = {
                "failure_rate": failure_rate,
                "failure_count": failure_count,
                "success_count": success_count,
                "threshold": threshold,
                "window_seconds": window_seconds,
            }
            return is_open, stats

        except Exception as e:
            logger.error(f"Circuit breaker check error for campaign {campaign_id}: {e}")
            return False, None

    async def record_and_evaluate(
        self,
        campaign_id: int,
        is_failure: bool,
        *,
        workflow_run_id: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Record a call outcome, and if the breaker trips, pause the campaign.

        This is the main entry point called from telephony status callbacks.
        It handles fetching campaign config, recording the outcome, and
        pausing + publishing an event if the breaker trips.

        ``workflow_run_id`` and ``reason`` are optional but should be supplied
        on failures: they are appended to a capped Redis list so the campaign
        log entry written on trip can name the calls that pushed the breaker
        over the threshold.

        Exceptions are caught internally so this never disrupts the caller.
        """
        try:
            campaign = await db_client.get_campaign_by_id(campaign_id)
            if not campaign or campaign.state != "running":
                return

            cb_config = {}
            if campaign.orchestrator_metadata:
                cb_config = campaign.orchestrator_metadata.get("circuit_breaker", {})

            if is_failure and workflow_run_id is not None:
                await self._push_recent_failure(
                    campaign_id=campaign_id,
                    workflow_run_id=workflow_run_id,
                    reason=reason,
                )

            tripped, stats = await self.record_call_outcome(
                campaign_id=campaign_id,
                is_failure=is_failure,
                config=cb_config,
            )

            if tripped and stats:
                logger.warning(
                    f"Circuit breaker tripped for campaign {campaign_id}, "
                    f"pausing campaign. Stats: {stats}"
                )

                recent_failures = await self._get_recent_failures(campaign_id)

                await db_client.update_campaign(campaign_id=campaign_id, state="paused")
                await db_client.append_campaign_log(
                    campaign_id=campaign_id,
                    level="warning",
                    event="circuit_breaker_tripped",
                    message=(
                        f"Paused: failure rate {stats['failure_rate']:.2%} "
                        f"({stats['failure_count']}/"
                        f"{stats['failure_count'] + stats['success_count']}) "
                        f"exceeded threshold {stats['threshold']:.2%} "
                        f"in {stats['window_seconds']}s window"
                    ),
                    details={**stats, "recent_failures": recent_failures},
                )

                publisher = await get_campaign_event_publisher()
                await publisher.publish_circuit_breaker_tripped(
                    campaign_id=campaign_id,
                    failure_rate=stats["failure_rate"],
                    failure_count=stats["failure_count"],
                    success_count=stats["success_count"],
                    threshold=stats["threshold"],
                    window_seconds=stats["window_seconds"],
                )

        except Exception as e:
            logger.error(f"Error in circuit breaker for campaign {campaign_id}: {e}")

    async def reset(self, campaign_id: int) -> bool:
        """Reset the circuit breaker state for a campaign.

        Called when a campaign is resumed to give it a clean slate. Also clears
        the recent-failures list so log entries from the next trip reference
        only post-resume failures.
        """
        redis_client = await self._get_redis()
        fail_key, succ_key = self._keys(campaign_id)
        recent_key = self._recent_failures_key(campaign_id)

        try:
            await redis_client.delete(fail_key, succ_key, recent_key)
            logger.info(f"Circuit breaker reset for campaign {campaign_id}")
            return True
        except Exception as e:
            logger.error(
                f"Error resetting circuit breaker for campaign {campaign_id}: {e}"
            )
            return False

    async def close(self):
        """Close Redis connection."""
        if self.redis_client:
            await self.redis_client.close()
            self.redis_client = None


# Global circuit breaker instance
circuit_breaker = CircuitBreaker()
