import time
import uuid
from dataclasses import dataclass
from typing import Optional

import redis.asyncio as aioredis
from loguru import logger

from api.constants import REDIS_URL

# Fleet-wide mirror of every live slot ("<org_id>:<slot_id>", scored by acquire
# time), maintained by the acquire/release paths alongside the per-org sets so
# the autoscaling scrape (get_fleet_concurrent_count) is a single ZCOUNT instead
# of a keyspace scan. Deliberately outside the "concurrent_calls:" prefix so it
# can never collide with an org or scope counter key.
FLEET_CONCURRENT_KEY = "concurrent_calls_fleet"


@dataclass(frozen=True)
class ConcurrentSlotAcquisition:
    slot_id: str
    active_count: int


class RateLimiter:
    """Sliding window rate limiter to enforce strict per-second limits and concurrent call limits"""

    def __init__(self):
        self.redis_client: Optional[aioredis.Redis] = None
        self.stale_call_timeout = 1200  # 20 minutes in seconds

    async def _get_redis(self) -> aioredis.Redis:
        """Get or create Redis connection"""
        if self.redis_client is None:
            self.redis_client = await aioredis.from_url(
                REDIS_URL, decode_responses=True
            )
        return self.redis_client

    async def acquire_token(
        self,
        organization_id: int,
        rate_limit: int = 1,
        *,
        scope_key: str | None = None,
    ) -> bool:
        """
        Enforces strict rate limit: max N calls per rolling second window
        Returns True if allowed, False if rate limited

        ``scope_key`` creates an isolated rate-limit bucket without touching
        organization concurrency counters or fleet-wide call metrics.
        """
        redis_client = await self._get_redis()

        key = f"rate_limit:{scope_key or organization_id}"
        now = time.time()
        window_start = now - 1.0  # 1 second sliding window

        # Lua script for atomic sliding window operation
        lua_script = """
        local key = KEYS[1]
        local now = tonumber(ARGV[1])
        local window_start = tonumber(ARGV[2])
        local max_requests = tonumber(ARGV[3])
        
        -- Remove timestamps older than window
        redis.call('ZREMRANGEBYSCORE', key, 0, window_start)
        
        -- Count requests in current window
        local current_requests = redis.call('ZCARD', key)
        
        if current_requests < max_requests then
            -- Add current timestamp
            redis.call('ZADD', key, now, ARGV[4])
            redis.call('EXPIRE', key, 2)  -- Expire after 2 seconds
            return 1
        else
            return 0
        end
        """

        try:
            result = await redis_client.eval(
                lua_script, 1, key, now, window_start, rate_limit, uuid.uuid4().hex
            )
            return bool(result)
        except Exception as e:
            logger.error(f"Rate limiter error: {e}")
            # On error, be conservative and deny
            return False

    async def get_next_available_slot(
        self, organization_id: int, rate_limit: int = 1, *, scope_key: str | None = None
    ) -> float:
        """
        Returns seconds until next available slot
        Useful for implementing retry with backoff
        """
        redis_client = await self._get_redis()

        key = f"rate_limit:{scope_key or organization_id}"

        try:
            # Get oldest timestamp in current window
            oldest = await redis_client.zrange(key, 0, 0, withscores=True)
            if not oldest:
                return 0.0  # Can call immediately

            oldest_time = oldest[0][1]
            next_available = oldest_time + 1.0  # 1 second after oldest
            wait_time = max(0, next_available - time.time())

            return wait_time
        except Exception as e:
            logger.error(f"Rate limiter get_next_available_slot error: {e}")
            return 1.0  # Default wait time on error

    async def try_acquire_concurrent_slot(
        self, organization_id: int, max_concurrent: int = 20
    ) -> Optional[str]:
        """
        Try to acquire a concurrent call slot.
        Returns a unique slot_id if successful, None if limit reached.
        """
        acquisition = await self.try_acquire_concurrent_slot_details(
            organization_id, max_concurrent
        )
        return acquisition.slot_id if acquisition else None

    async def try_acquire_concurrent_slot_details(
        self,
        organization_id: int,
        max_concurrent: int = 20,
        *,
        scope_key: str | None = None,
        scope_max_concurrent: int | None = None,
    ) -> Optional[ConcurrentSlotAcquisition]:
        """
        Try to acquire a concurrent call slot.
        Returns the slot_id and post-acquire active count if successful,
        or None if the limit is reached.

        When ``scope_key``/``scope_max_concurrent`` are provided, the slot is
        also registered in a secondary counter (``concurrent_calls:<scope_key>``,
        e.g. ``campaign:<id>``) and acquisition additionally requires that
        counter to be below ``scope_max_concurrent``. Both counters are
        updated atomically. The scope-scoped slot must be released with the
        same ``scope_key``.

        Every successful acquisition is also mirrored into the fleet-wide set
        (FLEET_CONCURRENT_KEY) in the same atomic script — see
        get_fleet_concurrent_count. Scope entries are not mirrored: a scoped
        call already has exactly one fleet member via its org slot.
        """
        redis_client = await self._get_redis()

        concurrent_key = f"concurrent_calls:{organization_id}"
        scope_concurrent_key = f"concurrent_calls:{scope_key}" if scope_key else ""
        now = time.time()
        stale_cutoff = now - self.stale_call_timeout

        # Lua script for atomic operation across the org counter and the
        # optional scope counter (empty scope key = org-only acquisition).
        lua_script = """
        local key = KEYS[1]
        local scope_key = KEYS[2]
        local fleet_key = KEYS[3]
        local now = tonumber(ARGV[1])
        local max_concurrent = tonumber(ARGV[2])
        local stale_cutoff = tonumber(ARGV[3])
        local slot_id = ARGV[4]
        local scope_max_concurrent = tonumber(ARGV[5])
        local fleet_member = ARGV[6]

        -- Remove stale entries (older than the stale-call timeout)
        redis.call('ZREMRANGEBYSCORE', key, 0, stale_cutoff)

        -- Get current count
        local current_count = redis.call('ZCARD', key)

        if current_count >= max_concurrent then
            return nil
        end

        if scope_key ~= '' then
            redis.call('ZREMRANGEBYSCORE', scope_key, 0, stale_cutoff)
            if redis.call('ZCARD', scope_key) >= scope_max_concurrent then
                return nil
            end
            redis.call('ZADD', scope_key, now, slot_id)
            redis.call('EXPIRE', scope_key, 3600)
        end

        redis.call('ZADD', key, now, slot_id)
        redis.call('EXPIRE', key, 3600)  -- Expire after 1 hour

        -- Mirror the slot into the fleet-wide set (autoscaling signal); stale
        -- members are pruned here since no other write path touches this key.
        redis.call('ZREMRANGEBYSCORE', fleet_key, 0, stale_cutoff)
        redis.call('ZADD', fleet_key, now, fleet_member)
        redis.call('EXPIRE', fleet_key, 3600)
        return {slot_id, current_count + 1}
        """

        # Generate unique slot ID (timestamp + random component)
        slot_id = f"{int(now * 1000)}_{uuid.uuid4().hex[:8]}"

        try:
            result = await redis_client.eval(
                lua_script,
                3,
                concurrent_key,
                scope_concurrent_key,
                FLEET_CONCURRENT_KEY,
                now,
                max_concurrent,
                stale_cutoff,
                slot_id,
                scope_max_concurrent if scope_max_concurrent is not None else 0,
                f"{organization_id}:{slot_id}",
            )
            if not result:
                return None

            acquired_slot_id, active_count = result
            return ConcurrentSlotAcquisition(
                slot_id=str(acquired_slot_id),
                active_count=int(active_count),
            )
        except Exception as e:
            logger.error(f"Concurrent limiter error: {e}")
            return None

    async def release_concurrent_slot(
        self,
        organization_id: int,
        slot_id: str,
        scope_key: str | None = None,
    ) -> bool | None:
        """
        Release a concurrent call slot (and its scope counter entry, if any).
        Returns True if the slot was released, False if it was already gone
        (released/stale-expired), or None on a Redis error — callers that
        track cleanup state should keep it around for retry when None.
        """
        if not slot_id:
            return False

        redis_client = await self._get_redis()
        concurrent_key = f"concurrent_calls:{organization_id}"

        try:
            removed = await redis_client.zrem(concurrent_key, slot_id)
            await redis_client.zrem(
                FLEET_CONCURRENT_KEY, f"{organization_id}:{slot_id}"
            )
            if scope_key:
                await redis_client.zrem(f"concurrent_calls:{scope_key}", slot_id)
            if removed:
                logger.debug(
                    f"Released concurrent slot {slot_id} for org {organization_id}"
                )
            return bool(removed)
        except Exception as e:
            logger.error(f"Error releasing concurrent slot: {e}")
            return None

    async def get_concurrent_count(self, organization_id: int) -> int:
        """
        Get current number of active concurrent calls for an organization.
        Automatically cleans up stale entries.
        """
        redis_client = await self._get_redis()
        concurrent_key = f"concurrent_calls:{organization_id}"

        try:
            # Clean up stale entries first
            stale_cutoff = time.time() - self.stale_call_timeout
            await redis_client.zremrangebyscore(concurrent_key, 0, stale_cutoff)

            # Get current count
            count = await redis_client.zcard(concurrent_key)
            return count
        except Exception as e:
            logger.error(f"Error getting concurrent count: {e}")
            return 0

    async def get_fleet_concurrent_count(self) -> int:
        """Total active calls across every org — the fleet-wide autoscaling signal.

        One ZCOUNT over FLEET_CONCURRENT_KEY, the fleet-wide mirror the
        acquire/release paths maintain alongside the per-org counters — no
        keyspace scan, no per-org fan-out, and scrape cost is independent of
        whatever else lives in this (shared) Redis. Counting by score (not
        ZCARD) excludes slots older than stale_call_timeout without writing, so
        an orphaned call can't keep the metric high and block scale-down,
        matching the org counters' stale semantics.

        Unlike the sibling methods, Redis errors are NOT swallowed here: for an
        autoscaling signal, 0 is the most aggressive scale-down instruction, so
        a failed read must surface as an error (the autoscale-metric endpoint
        turns it into a 503) rather than masquerade as an idle fleet.
        """
        redis_client = await self._get_redis()
        stale_cutoff = time.time() - self.stale_call_timeout
        return await redis_client.zcount(FLEET_CONCURRENT_KEY, stale_cutoff, "+inf")

    async def store_workflow_slot_mapping(
        self, workflow_run_id: int, organization_id: int, slot_id: str
    ) -> bool:
        """
        Store the mapping between workflow_run_id and its concurrent slot.
        Used for cleanup when calls complete.
        """
        redis_client = await self._get_redis()
        mapping_key = f"workflow_slot_mapping:{workflow_run_id}"

        try:
            # Store as a hash with TTL
            await redis_client.hset(
                mapping_key, mapping={"org_id": organization_id, "slot_id": slot_id}
            )
            # Set expiry to match stale timeout
            await redis_client.expire(mapping_key, self.stale_call_timeout)
            return True
        except Exception as e:
            logger.error(f"Error storing workflow slot mapping: {e}")
            return False

    async def store_workflow_slot_mapping_if_absent(
        self,
        workflow_run_id: int,
        organization_id: int,
        slot_id: str,
        scope_key: str | None = None,
    ) -> bool:
        """
        Store the workflow_run_id -> concurrent slot mapping only if no mapping
        already exists. This prevents duplicate public/WebRTC starts for the
        same workflow run from overwriting the cleanup pointer.
        """
        redis_client = await self._get_redis()
        mapping_key = f"workflow_slot_mapping:{workflow_run_id}"

        lua_script = """
        local key = KEYS[1]
        local org_id = ARGV[1]
        local slot_id = ARGV[2]
        local ttl = tonumber(ARGV[3])
        local scope_key = ARGV[4]

        if redis.call('EXISTS', key) == 1 then
            return 0
        end

        redis.call('HSET', key, 'org_id', org_id, 'slot_id', slot_id)
        if scope_key ~= '' then
            redis.call('HSET', key, 'scope_key', scope_key)
        end
        redis.call('EXPIRE', key, ttl)
        return 1
        """

        try:
            stored = await redis_client.eval(
                lua_script,
                1,
                mapping_key,
                organization_id,
                slot_id,
                self.stale_call_timeout,
                scope_key or "",
            )
            return bool(stored)
        except Exception as e:
            logger.error(f"Error storing workflow slot mapping if absent: {e}")
            return False

    async def get_workflow_slot_mapping(
        self, workflow_run_id: int
    ) -> Optional[tuple[int, str, str | None]]:
        """
        Get the concurrent slot mapping for a workflow run.
        Returns (organization_id, slot_id, scope_key) or None if not found;
        scope_key is None for slots acquired without a scope counter.
        """
        redis_client = await self._get_redis()
        mapping_key = f"workflow_slot_mapping:{workflow_run_id}"

        try:
            mapping = await redis_client.hgetall(mapping_key)
            if mapping and "org_id" in mapping and "slot_id" in mapping:
                return (
                    int(mapping["org_id"]),
                    mapping["slot_id"],
                    mapping.get("scope_key") or None,
                )
            return None
        except Exception as e:
            logger.error(f"Error getting workflow slot mapping: {e}")
            return None

    async def reconcile_workflow_slot_mapping(
        self,
        workflow_run_id: int,
        *,
        organization_id: int,
        slot_id: str | None = None,
        scope_key: str | None = None,
    ) -> None:
        """Durable cleanup, including after the workflow mapping's TTL expires.

        Errors propagate so the DB cleanup marker stays pending until every
        counter and the mapping have been cleared. Repeating cleanup is safe.
        """
        redis_client = await self._get_redis()
        mapping_key = f"workflow_slot_mapping:{workflow_run_id}"
        mapping = await redis_client.hgetall(mapping_key)
        slots = {(slot_id, scope_key)} if slot_id else set()
        if mapping:
            if int(mapping["org_id"]) != organization_id:
                raise ValueError(
                    f"Slot mapping organization mismatch for run {workflow_run_id}"
                )
            slots.add((mapping["slot_id"], mapping.get("scope_key") or None))
        for reserved_slot_id, reserved_scope in slots:
            released = await self.release_concurrent_slot(
                organization_id, reserved_slot_id, scope_key=reserved_scope
            )
            if released is None:
                raise ConnectionError(f"Slot cleanup failed for run {workflow_run_id}")
        await redis_client.delete(mapping_key)

    async def delete_workflow_slot_mapping(self, workflow_run_id: int) -> bool:
        """
        Delete the workflow slot mapping after releasing the slot.
        """
        redis_client = await self._get_redis()
        mapping_key = f"workflow_slot_mapping:{workflow_run_id}"

        try:
            deleted = await redis_client.delete(mapping_key)
            return bool(deleted)
        except Exception as e:
            logger.error(f"Error deleting workflow slot mapping: {e}")
            return False

    async def select_from_number(
        self,
        organization_id: int,
        telephony_configuration_id: int | None,
        from_numbers: list[str],
    ) -> str | None:
        """Rotate active caller IDs without reserving them for a call's lifetime."""
        numbers = sorted(set(from_numbers))
        if not numbers:
            return None
        key = f"caller_id_rotation:{organization_id}:{telephony_configuration_id}"
        try:
            redis_client = await self._get_redis()
            index = await redis_client.eval(
                "local n = redis.call('INCR', KEYS[1]); "
                "redis.call('EXPIRE', KEYS[1], 86400); return n - 1",
                1,
                key,
            )
            return numbers[int(index) % len(numbers)]
        except Exception as exc:
            logger.warning(
                f"Caller-ID rotation unavailable for config {telephony_configuration_id}: {exc}"
            )
            return numbers[0]

    async def close(self):
        """Close Redis connection"""
        if self.redis_client:
            await self.redis_client.close()
            self.redis_client = None


# Global rate limiter instance
rate_limiter = RateLimiter()
