"""Pause campaigns on provider HTTP 401/402/403 or high pipeline failure rates.

Provider rejections pause the campaign directly using its existing state and
logs. Resuming the campaign clears that pause without separate breaker state.

Uses two Redis sorted sets (ZSETs) per campaign — one for failures, one for
successes — as sliding windows, keyed by workflow run ID so duplicate outcomes
cannot inflate the ratio or count a run on both sides. Only pipeline
finalization records outcomes; carrier results never enter this window.

A separate capped Redis list (``cb_pipeline_recent_failures:{campaign_id}``) stores the
last N failing ``{workflow_run_id, reason, ts}`` entries so the campaign log
written when the breaker trips can show *which* calls pushed it over.
"""

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import redis.asyncio as aioredis
from loguru import logger
from pipecat.utils.errors import ErrorCategory, extract_http_status_code
from pipecat.utils.run_context import get_current_org_id, get_current_run_id

from api.constants import DEFAULT_CIRCUIT_BREAKER_CONFIG, REDIS_URL
from api.db import db_client
from api.errors.failure import extract_http_status
from api.services.campaign.campaign_event_publisher import get_campaign_event_publisher

# Cap on the number of recent failure entries kept per campaign — large enough
# to be useful for debugging a trip, small enough that the JSON details stay
# bounded.
MAX_RECENT_FAILURES = 20

PROVIDER_PAUSE_STATUSES = {401, 402, 403}
_HTTP_STATUS_RE = re.compile(
    r"\b(?:http(?:\s+status)?|status(?:_code)?|error\s+code)\s*[:=]?\s*(\d{3})\b",
    re.IGNORECASE,
)


def provider_http_status(exception=None, message=""):
    """Read protocol status, including Deepgram's string-only WebSocket errors."""
    if exception is not None:
        status = extract_http_status(exception) or extract_http_status_code(exception)
        if status is not None:
            return status
        message = f"{message} {exception}"
    statuses = [int(match) for match in _HTTP_STATUS_RE.findall(message)]
    return next(
        (status for status in statuses if status in PROVIDER_PAUSE_STATUSES),
        statuses[0] if statuses else None,
    )


def register_campaign_provider_error_handler(service, *, provider, source):
    """Capture the run context before a provider or fallback starts its own tasks."""
    run_id, org_id = get_current_run_id(), get_current_org_id()
    if run_id is None or org_id is None:
        return

    async def on_error(_processor, frame):
        if frame.category == ErrorCategory.APPLICATION:
            return  # A tool's HTTP error is not a model provider failure.
        await circuit_breaker.pause_on_provider_error(
            organization_id=int(org_id),
            workflow_run_id=int(run_id),
            provider=str(getattr(provider, "value", provider) or "unknown"),
            service=source.value,
            exception=frame.exception,
            message=frame.error,
        )

    # This awaited hook runs even when a fallback consumes the ErrorFrame.
    service.add_event_handler("on_error", on_error)


class CircuitBreaker:
    """Sliding window circuit breaker for completed campaign pipelines."""

    def __init__(self):
        self.redis_client: Optional[aioredis.Redis] = None

    async def pause_on_provider_error(
        self,
        *,
        organization_id: int,
        workflow_run_id: int,
        provider: str,
        service: str,
        exception=None,
        message="",
    ) -> None:
        """401/402/403 pause the affected campaign without a sample-size threshold.

        No account/key state is retained. API-triggered runs have no campaign,
        so the scoped DB operation leaves them and other campaigns unchanged.
        """
        status = provider_http_status(exception, message)
        if status not in PROVIDER_PAUSE_STATUSES:
            return
        try:
            campaign_id = await db_client.pause_campaign_for_provider_error(
                organization_id,
                workflow_run_id,
                provider=provider,
                service=service,
                status_code=status,
            )
            if campaign_id is not None:
                logger.warning(
                    "Paused campaign {}: {} {} returned HTTP {} (run {})",
                    campaign_id,
                    provider,
                    service,
                    status,
                    workflow_run_id,
                )
        except Exception as exc:
            logger.error(
                "Could not pause campaign for provider HTTP {} on run {} ({})",
                status,
                workflow_run_id,
                type(exc).__name__,
            )

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
        # New keys keep legacy carrier outcomes out during rolling upgrades.
        return (
            f"cb_pipeline_failures:{campaign_id}",
            f"cb_pipeline_successes:{campaign_id}",
        )

    @staticmethod
    def _recent_failures_key(campaign_id: int) -> str:
        """Return the Redis key used for the capped recent-failures list."""
        return f"cb_pipeline_recent_failures:{campaign_id}"

    async def _get_recent_failures(
        self, campaign_id: int, *, since: float | None = None
    ) -> List[Dict[str, Any]]:
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
                entry = json.loads(raw)
                if since is None or entry["ts"] > since:
                    decoded.append(entry)
            except (KeyError, TypeError, ValueError):
                continue
        return decoded

    async def record_call_outcome(
        self,
        campaign_id: int,
        is_failure: bool,
        config: Optional[dict] = None,
        *,
        workflow_run_id: int,
        reason: Optional[str] = None,
    ) -> Tuple[bool, Optional[dict]]:
        """Record a call outcome and check if the circuit breaker should trip.

        Args:
            campaign_id: The campaign ID.
            is_failure: True if the pipeline failed, False if it ended normally.
            config: Optional per-campaign circuit breaker config override.
                    Falls back to DEFAULT_CIRCUIT_BREAKER_CONFIG.
            workflow_run_id: Identity of the final outcome, required on both sides.
            reason: Final pipeline termination reason, retained for failures.

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
        recent_key = self._recent_failures_key(campaign_id)

        lua_script = """
        local fail_key = KEYS[1]
        local succ_key = KEYS[2]
        local recent_key = KEYS[3]
        local now = tonumber(ARGV[1])
        local window_start = tonumber(ARGV[2])
        local is_failure = tonumber(ARGV[3])
        local threshold = tonumber(ARGV[4])
        local min_calls = tonumber(ARGV[5])
        local ttl = tonumber(ARGV[6])
        local run_id = ARGV[7]
        local recent_failure = ARGV[8]
        local max_recent = tonumber(ARGV[9])

        -- A repeated finalization must not move the original event forward in
        -- time or put a call in both sets. Check before trimming expired events.
        local recorded = redis.call('ZSCORE', fail_key, run_id)
            or redis.call('ZSCORE', succ_key, run_id)

        -- Trim both sets to the sliding window
        redis.call('ZREMRANGEBYSCORE', fail_key, 0, window_start)
        redis.call('ZREMRANGEBYSCORE', succ_key, 0, window_start)

        -- Record the outcome and diagnostics together, once per run.
        if not recorded then
            if is_failure == 1 then
                redis.call('ZADD', fail_key, now, run_id)
                redis.call('LPUSH', recent_key, recent_failure)
                redis.call('LTRIM', recent_key, 0, max_recent - 1)
                redis.call('EXPIRE', recent_key, ttl)
            else
                redis.call('ZADD', succ_key, now, run_id)
            end
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
                3,
                fail_key,
                succ_key,
                recent_key,
                now,
                window_start,
                1 if is_failure else 0,
                threshold,
                min_calls,
                window_seconds + 60,  # TTL with buffer
                workflow_run_id,
                json.dumps(
                    {"workflow_run_id": workflow_run_id, "reason": reason, "ts": now}
                ),
                MAX_RECENT_FAILURES,
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
        workflow_run_id: int,
        reason: Optional[str] = None,
    ) -> None:
        """Record a call outcome, and if the breaker trips, pause the campaign.

        This is the main entry point called from pipeline finalization.
        It handles fetching campaign config, recording the outcome, and
        pausing + publishing an event if the breaker trips.

        ``workflow_run_id`` deduplicates both successes and failures. Failed
        runs and their reasons are also appended to a capped Redis list so the
        trip log can name the calls that pushed it over the threshold.

        Exceptions are caught internally so this never disrupts the caller.
        """
        try:
            campaign = await db_client.get_campaign_by_id(campaign_id)
            if not campaign or campaign.state != "running":
                return

            cb_config = {}
            if campaign.orchestrator_metadata:
                cb_config = campaign.orchestrator_metadata.get("circuit_breaker", {})

            tripped, stats = await self.record_call_outcome(
                campaign_id=campaign_id,
                is_failure=is_failure,
                config=cb_config,
                workflow_run_id=workflow_run_id,
                reason=reason,
            )

            if tripped and stats:
                logger.warning(
                    f"Circuit breaker tripped for campaign {campaign_id}, "
                    f"pausing campaign. Stats: {stats}"
                )

                recent_failures = await self._get_recent_failures(
                    campaign_id, since=time.time() - stats["window_seconds"]
                )

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
