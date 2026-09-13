from __future__ import annotations

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


@dataclass(frozen=True)
class FromNumberAcquisition:
    """A from_number plus the ownership token (acquisition-time score) that
    was written for it in the pool ZSET.

    The token must be carried into the workflow's from_number mapping and
    passed back to ``release_from_number`` as ``expected_token`` so a stale
    retry can never free a number that another call has since re-acquired
    (it only frees the number when the pool's current score still equals
    this token).
    """

    from_number: str
    token: str


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
            redis.call('ZADD', key, now, now)
            redis.call('EXPIRE', key, 2)  -- Expire after 2 seconds
            return 1
        else
            return 0
        end
        """

        try:
            result = await redis_client.eval(
                lua_script, 1, key, now, window_start, rate_limit
            )
            return bool(result)
        except Exception as e:
            logger.error(f"Rate limiter error: {e}")
            # On error, be conservative and deny
            return False

    async def get_next_available_slot(
        self, organization_id: int, rate_limit: int = 1
    ) -> float:
        """
        Returns seconds until next available slot
        Useful for implementing retry with backoff
        """
        redis_client = await self._get_redis()

        key = f"rate_limit:{organization_id}"

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

    # ======== FROM NUMBER POOL METHODS ========

    @staticmethod
    def _from_number_pool_key(
        organization_id: int, telephony_configuration_id: int | None
    ) -> str:
        return f"from_number_pool:{organization_id}:{telephony_configuration_id}"

    async def initialize_from_number_pool(
        self,
        organization_id: int,
        from_numbers: list[str],
        telephony_configuration_id: int | None,
    ) -> bool:
        """
        Initialize the from_number pool for an organization + telephony config.
        Uses ZADD NX so it won't overwrite numbers that are already in use.

        Pools are scoped per (organization_id, telephony_configuration_id) so
        that orgs with multiple telephony configurations do not leak caller IDs
        across configs.
        """
        if not from_numbers:
            return False

        redis_client = await self._get_redis()
        key = self._from_number_pool_key(organization_id, telephony_configuration_id)

        try:
            # ZADD NX: only add members that don't already exist (preserves in-use scores)
            members = {number: 0 for number in from_numbers}
            await redis_client.zadd(key, members, nx=True)
            await redis_client.expire(key, 3600)  # 1 hour TTL
            return True
        except Exception as e:
            logger.error(f"Error initializing from_number pool: {e}")
            return False

    _ACQUIRE_FROM_NUMBER_LUA = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local stale_cutoff = tonumber(ARGV[2])

    -- Clean stale entries: members with score > 0 and score < stale_cutoff
    local stale = redis.call('ZRANGEBYSCORE', key, 1, stale_cutoff)
    for i, member in ipairs(stale) do
        redis.call('ZADD', key, 0, member)
    end

    -- Find all available numbers (score == 0)
    local available = redis.call('ZRANGEBYSCORE', key, 0, 0)
    if #available == 0 then
        return nil
    end

    -- Pick a random number from the available pool for uniform distribution
    local idx = math.random(#available)
    local chosen = available[idx]

    -- Atomic sequence counter per pool key for monotonic uniqueness
    local seq = redis.call('INCR', key .. ':seq')
    redis.call('EXPIRE', key .. ':seq', 3600)

    -- Build unique score: floor(now) seconds + (seq % 1000000) microseconds
    local score = math.floor(now) + (seq % 1000000) * 0.000001
    local score_str = string.format("%.6f", score)

    -- Mark as in-use with unique timestamp score
    redis.call('ZADD', key, score, chosen)
    return {chosen, score_str}
    """

    async def _acquire_from_number_raw(
        self, organization_id: int, telephony_configuration_id: int | None
    ) -> tuple[Optional[str], Optional[str]]:
        """Shared implementation for acquire_from_number and
        acquire_from_number_with_token. Returns (from_number_or_None, token_or_None) --
        `token` is the unique score the Lua script atomically generated and ZADD'ed
        for the returned number, ensuring collision-free ownership.
        """
        redis_client = await self._get_redis()
        key = self._from_number_pool_key(organization_id, telephony_configuration_id)
        now = time.time()
        stale_cutoff = now - self.stale_call_timeout

        try:
            result = await redis_client.eval(
                self._ACQUIRE_FROM_NUMBER_LUA, 1, key, now, stale_cutoff
            )
            if result:
                if isinstance(result, (list, tuple)) and len(result) >= 2:
                    from_number = result[0]
                    token = str(result[1])
                elif isinstance(result, str):
                    from_number = result
                    token = str(now)
                else:
                    return None, None
                logger.debug(
                    f"Acquired from_number {from_number} for org {organization_id}"
                )
                return from_number, token
            return None, None
        except Exception as e:
            logger.error(f"Error acquiring from_number: {e}")
            return None, None

    async def acquire_from_number(
        self, organization_id: int, telephony_configuration_id: int | None
    ) -> Optional[str]:
        """
        Atomically acquire an available from_number from the pool for the given
        (organization_id, telephony_configuration_id).
        Cleans stale entries (score > 0 and older than 30 min) before acquiring.

        Returns the phone number if available, None if all numbers are in use.

        See ``acquire_from_number_with_token`` for a variant that also
        surfaces the ownership token new call sites should carry through the
        workflow's from_number mapping so releases can be made race-safe.
        """
        from_number, _token = await self._acquire_from_number_raw(
            organization_id, telephony_configuration_id
        )
        return from_number

    async def acquire_from_number_with_token(
        self, organization_id: int, telephony_configuration_id: int | None
    ) -> Optional[FromNumberAcquisition]:
        """
        Same as acquire_from_number, but also returns the ownership token
        (the acquisition-time score) written for the number. Callers must
        carry ``.token`` into the workflow's from_number mapping
        (store_workflow_from_number_mapping) and pass it back on release
        (release_from_number's ``expected_token``) so a release can never
        free a number that has since been re-acquired by another call.
        """
        from_number, token = await self._acquire_from_number_raw(
            organization_id, telephony_configuration_id
        )
        if from_number is None or token is None:
            return None
        return FromNumberAcquisition(from_number=from_number, token=token)

    async def release_from_number(
        self,
        organization_id: int,
        from_number: str,
        telephony_configuration_id: int | None,
        expected_token: str | None = None,
    ) -> bool | None:
        """
        Release a from_number back to its (org, telephony config) pool by
        setting its score to 0.

        When ``expected_token`` is given (the ownership token returned by
        acquire_from_number and carried through the workflow's from_number
        mapping), the release is conditional on the pool's current score
        still matching that token: the Lua script distinguishes
        "absent from the pool" (0), "released by us" (1), and "present but
        the score no longer matches our token" (2, e.g. it was already
        released and/or re-acquired by another call since) -- outcome 2
        leaves the pool untouched so a stale retry can never free a number
        someone else is now using. When ``expected_token`` is omitted
        (legacy mappings written before the token field existed), the
        release is unconditional, matching the original behaviour -- note
        that in Lua a score of 0 is truthy, so a number that is already
        released still takes the "release" branch and returns 1; that is
        NOT a signal that the number was absent.

        Returns True if released by us (outcome 1), False if the number was
        absent from the pool or is owned by someone else (outcomes 0/2 --
        both mean "nothing more for us to do here"), or None on a Redis
        error. Because the token check makes a release a no-op unless we
        still own the number, a None result is now safe to retry: retrying
        can only ever re-attempt freeing OUR OWN acquisition, never someone
        else's.
        """
        if not from_number:
            return False

        redis_client = await self._get_redis()
        key = self._from_number_pool_key(organization_id, telephony_configuration_id)

        lua_script = """
        local key = KEYS[1]
        local from_number = ARGV[1]
        local expected_token = ARGV[2]

        local score = redis.call('ZSCORE', key, from_number)
        if not score then
            return 0
        end

        if expected_token == '' then
            -- No ownership token supplied (legacy mapping): unconditional
            -- release, matching pre-token behaviour. Note 0 is truthy in
            -- Lua, so an already-released number (score 0) still lands
            -- here and returns 1 -- it does not mean "absent".
            redis.call('ZADD', key, 0, from_number)
            return 1
        end

        if tonumber(score) == tonumber(expected_token) then
            redis.call('ZADD', key, 0, from_number)
            return 1
        end

        -- Present but the score no longer matches our token: either it was
        -- already released (score 0) or re-acquired by another call (score
        -- is their newer timestamp). Either way, do not touch the pool.
        return 2
        """

        try:
            result = await redis_client.eval(
                lua_script, 1, key, from_number, expected_token or ""
            )
            if result == 1:
                logger.debug(
                    f"Released from_number {from_number} for org {organization_id}"
                )
            elif result == 2:
                logger.debug(
                    f"from_number {from_number} for org {organization_id} was "
                    "not released: current score no longer matches the "
                    "expected ownership token (already released and/or "
                    "re-acquired by another call)"
                )
            return result == 1
        except Exception as e:
            logger.error(f"Error releasing from_number: {e}")
            return None

    async def store_workflow_from_number_mapping(
        self,
        workflow_run_id: int,
        organization_id: int,
        from_number: str,
        telephony_configuration_id: int | None,
        token: str | None = None,
    ) -> bool:
        """
        Store the mapping between workflow_run_id and its from_number, plus
        the telephony_configuration_id so cleanup can release back to the
        correct pool.

        ``token`` is the ownership token from acquire_from_number_with_token
        (the acquisition-time score). It is stored as an extra hash field so
        a later release can be made conditional on still owning the number;
        this is additive -- mappings written before this field existed (or
        by call sites still using the plain acquire_from_number) simply lack
        it, and release_from_number falls back to its unconditional legacy
        behaviour when no token is supplied.
        """
        redis_client = await self._get_redis()
        mapping_key = f"workflow_from_number:{workflow_run_id}"

        try:
            # Redis hashes can't store None — use empty string sentinel for legacy
            # campaigns whose telephony_configuration_id has not been backfilled.
            tcid_value = (
                "" if telephony_configuration_id is None else telephony_configuration_id
            )
            mapping = {
                "org_id": organization_id,
                "from_number": from_number,
                "telephony_configuration_id": tcid_value,
                "token": token or "",
            }
            await redis_client.hset(mapping_key, mapping=mapping)
            await redis_client.expire(mapping_key, 1800)  # 30 min TTL
            return True
        except Exception as e:
            logger.error(f"Error storing workflow from_number mapping: {e}")
            return False

    async def _get_workflow_from_number_mapping_raw(
        self, workflow_run_id: int
    ) -> Optional[tuple[int, str, int | None, str | None]]:
        """Shared implementation for get_workflow_from_number_mapping and
        get_workflow_from_number_mapping_with_token."""
        redis_client = await self._get_redis()
        mapping_key = f"workflow_from_number:{workflow_run_id}"

        try:
            mapping = await redis_client.hgetall(mapping_key)
            if mapping and "org_id" in mapping and "from_number" in mapping:
                raw_tcid = mapping.get("telephony_configuration_id", "")
                tcid = int(raw_tcid) if raw_tcid not in (None, "") else None
                token = mapping.get("token") or None
                return (int(mapping["org_id"]), mapping["from_number"], tcid, token)
            return None
        except Exception as e:
            logger.error(f"Error getting workflow from_number mapping: {e}")
            return None

    async def get_workflow_from_number_mapping(
        self, workflow_run_id: int
    ) -> Optional[tuple[int, str, int | None]]:
        """
        Get the from_number mapping for a workflow run.
        Returns (organization_id, from_number, telephony_configuration_id) or
        None if not found. telephony_configuration_id is None for legacy entries.

        See ``get_workflow_from_number_mapping_with_token`` for a variant
        that also surfaces the ownership token for a race-safe release.
        """
        raw = await self._get_workflow_from_number_mapping_raw(workflow_run_id)
        if raw is None:
            return None
        org_id, from_number, tcid, _token = raw
        return (org_id, from_number, tcid)

    async def get_workflow_from_number_mapping_with_token(
        self, workflow_run_id: int
    ) -> Optional[tuple[int, str, int | None, str | None]]:
        """
        Same as get_workflow_from_number_mapping, but also returns the
        ownership token stored alongside the mapping (None for mappings
        written before the token field existed, i.e. no token was recorded
        at acquire time). Pass the token through to release_from_number's
        ``expected_token`` so the release cannot free a number that has
        since been re-acquired by another call.
        """
        return await self._get_workflow_from_number_mapping_raw(workflow_run_id)

    async def delete_workflow_from_number_mapping(self, workflow_run_id: int) -> bool:
        """
        Delete the workflow from_number mapping after releasing the number.
        """
        redis_client = await self._get_redis()
        mapping_key = f"workflow_from_number:{workflow_run_id}"

        try:
            deleted = await redis_client.delete(mapping_key)
            return bool(deleted)
        except Exception as e:
            logger.error(f"Error deleting workflow from_number mapping: {e}")
            return False

    async def close(self):
        """Close Redis connection"""
        if self.redis_client:
            await self.redis_client.close()
            self.redis_client = None


# Global rate limiter instance
rate_limiter = RateLimiter()
