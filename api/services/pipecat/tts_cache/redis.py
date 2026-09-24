"""Reserve three contributors before synthesis, then atomically cache the median."""

import json

from redis.asyncio import Redis

from api.services.pipecat.tts_cache.models import CachePolicy, SynthesisRequest

_GET = """
local len = redis.call('STRLEN', KEYS[2])
-- Warming pools are misses. A lookup must not discard or count their samples.
if len == 0 and redis.call('EXISTS', KEYS[4], KEYS[5]) > 0 then return false end
if len == 0 or len > tonumber(ARGV[1]) then
    redis.call('DEL', KEYS[2], KEYS[3], KEYS[4], KEYS[5])
    redis.call('ZREM', KEYS[1], KEYS[2])
    return false
end
local value = redis.call('GET', KEYS[2])
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local ttl = math.ceil(tonumber(ARGV[2]))
redis.call('EXPIRE', KEYS[2], ttl)
redis.call('ZADD', KEYS[1], now, KEYS[2])
redis.call('EXPIRE', KEYS[1], math.max(ttl + 1, redis.call('TTL', KEYS[1])))
if redis.call('HEXISTS', KEYS[3], 'details') == 1 then
    redis.call('HINCRBY', KEYS[3], 'hits', 1)
    redis.call('HSET', KEYS[3], 'last_used_at', tostring(now))
    redis.call('EXPIRE', KEYS[3], ttl)
end
return value
"""

# KEYS throughout: org index, selected PCM, metadata, candidates, reservations.
# A malformed pool invalidates its leases too, fencing off in-flight writers.
_POOL_HELPERS = """
local function reset_pool()
    redis.call('DEL', KEYS[4], KEYS[5])
    redis.call('ZREM', KEYS[1], KEYS[2])
end
local function pool_size()
    local kind = redis.call('TYPE', KEYS[4]).ok
    local reservations_kind = redis.call('TYPE', KEYS[5]).ok
    if (kind ~= 'none' and kind ~= 'list') or
        (reservations_kind ~= 'none' and reservations_kind ~= 'zset') then
        reset_pool()
        return 0
    end
    local length = redis.call('LLEN', KEYS[4])
    if length ~= 0 and length ~= 3 and length ~= 6 then
        reset_pool()
        return 0
    end
    for offset = 0, length - 1, 3 do
        local duration = tonumber(redis.call('LINDEX', KEYS[4], offset))
        local ok, details = pcall(cjson.decode, redis.call('LINDEX', KEYS[4], offset + 2))
        if not duration or duration ~= duration or duration <= 0 or duration >= math.huge or
            not ok or type(details) ~= 'table' then
            reset_pool()
            return 0
        end
    end
    return length / 3
end
local function refresh_pool_expiry(now, ttl)
    -- Preserve completed takes until every active contributor has had time to
    -- finish, even when reservations outlive the normal cache idle TTL.
    local expires_at = now + ttl
    local latest = redis.call('ZREVRANGE', KEYS[5], 0, 0, 'WITHSCORES')
    if latest[2] then expires_at = math.max(expires_at, tonumber(latest[2])) end
    redis.call('PEXPIREAT', KEYS[4], math.ceil(expires_at * 1000))
end
"""

_CLAIM = (
    _POOL_HELPERS
    + """
if redis.call('EXISTS', KEYS[2]) == 1 then return {0, 0} end
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local ttl = math.ceil(tonumber(ARGV[2]))
local cap = tonumber(ARGV[3])
local drain = tonumber(ARGV[4])
local lease_seconds = tonumber(ARGV[5])
local count = pool_size()
redis.call('ZREMRANGEBYSCORE', KEYS[5], '-inf', now)
-- A lease can outlive a short cache TTL. Retain its index membership so clear
-- and eviction can still fence off the worker holding it.
local stale = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', now - ttl)
for _, key in ipairs(stale) do
    if redis.call('EXISTS', key, key .. ':candidates', key .. ':reservations') == 0 then
        redis.call('ZREM', KEYS[1], key)
    end
end
local evicted = 0
-- Existing pools also enforce a reduced cap, while protecting their own slot.
local target = cap - 1
if redis.call('ZSCORE', KEYS[1], KEYS[2]) then target = cap end
local victims = redis.call('ZRANGE', KEYS[1], 0, drain)
for _, victim in ipairs(victims) do
    if redis.call('ZCARD', KEYS[1]) <= target or evicted >= drain then break end
    if victim ~= KEYS[2] then
        redis.call('ZREM', KEYS[1], victim)
        redis.call('DEL', victim, victim .. ':meta', victim .. ':candidates',
            victim .. ':reservations')
        evicted = evicted + 1
    end
end
if redis.call('ZCARD', KEYS[1]) > target then return {0, evicted} end
if count + redis.call('ZCARD', KEYS[5]) >= 3 then return {0, evicted} end
redis.call('ZADD', KEYS[5], now + lease_seconds, ARGV[1])
redis.call('EXPIRE', KEYS[5], math.ceil(lease_seconds))
refresh_pool_expiry(now, ttl)
redis.call('ZADD', KEYS[1], now, KEYS[2])
redis.call('EXPIRE', KEYS[1], math.max(ttl + 1, math.ceil(lease_seconds) + 1,
    redis.call('TTL', KEYS[1])))
return {1, evicted}
"""
)

_PUT = (
    _POOL_HELPERS
    + """
-- Stale, expired, released, evicted and pre-invalidation tokens cannot write.
if redis.call('EXISTS', KEYS[2]) == 1 then return {0, 0} end
local count = pool_size()
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local deadline = tonumber(redis.call('ZSCORE', KEYS[5], ARGV[4]))
if not deadline or deadline <= now then
    redis.call('ZREM', KEYS[5], ARGV[4])
    return {0, 0}
end
local ttl = math.ceil(tonumber(ARGV[2]))
count = count + 1
if count == 3 then
    -- Build the final triplet in memory. A promotion error must not leave a
    -- three-member pool that grows forever and never attempts selection again.
    local pool = redis.call('LRANGE', KEYS[4], 0, -1)
    table.insert(pool, ARGV[5])
    table.insert(pool, ARGV[1])
    table.insert(pool, ARGV[3])
    local order = {1, 4, 7}
    table.sort(order, function(a, b)
        local left, right = tonumber(pool[a]), tonumber(pool[b])
        if left == right then return a < b end
        return left < right
    end)
    local selected = order[2]
    local details = cjson.decode(pool[selected + 2])
    details.selection_policy = 'reserved_median_duration_v1'
    details.candidate_duration_seconds = {
        tonumber(pool[1]), tonumber(pool[4]), tonumber(pool[7])
    }
    details.selected_candidate = (selected + 2) / 3
    redis.call('SET', KEYS[2], pool[selected + 1], 'EX', ttl)
    redis.call('HSET', KEYS[3], 'details', cjson.encode(details), 'hits', 0,
        'created_at', tostring(now), 'last_used_at', tostring(now))
    redis.call('EXPIRE', KEYS[3], ttl)
    redis.call('DEL', KEYS[4], KEYS[5])
else
    redis.call('RPUSH', KEYS[4], ARGV[5], ARGV[1], ARGV[3])
    redis.call('ZREM', KEYS[5], ARGV[4])
    refresh_pool_expiry(now, ttl)
end
redis.call('ZADD', KEYS[1], now, KEYS[2])
redis.call('EXPIRE', KEYS[1], math.max(ttl + 1, redis.call('TTL', KEYS[5]) + 1,
    redis.call('TTL', KEYS[1])))
return {count, 0}
"""
)

_RELEASE = """
if redis.call('TYPE', KEYS[5]).ok ~= 'zset' then return 0 end
redis.call('ZREM', KEYS[5], ARGV[1])
if redis.call('EXISTS', KEYS[2], KEYS[4], KEYS[5]) == 0 then
    redis.call('ZREM', KEYS[1], KEYS[2])
    redis.call('DEL', KEYS[3])
end
return 1
"""

_DELETE = """
if #ARGV > 0 and redis.call('GET', KEYS[2]) ~= ARGV[1] then return 0 end
local removed = math.min(1, redis.call('DEL', KEYS[2], KEYS[4], KEYS[5]))
redis.call('DEL', KEYS[3])
redis.call('ZREM', KEYS[1], KEYS[2])
return removed
"""

_INVALIDATE = """
local entries = redis.call('ZRANGE', KEYS[1], 0, -1)
local removed = 0
for _, key in ipairs(entries) do
    removed = removed + math.min(1, redis.call('DEL', key, key .. ':candidates',
        key .. ':reservations'))
    redis.call('DEL', key .. ':meta')
end
redis.call('DEL', KEYS[1])
return removed
"""

_LIST = """
local entries = redis.call('ZRANGE', KEYS[1], 0, tonumber(ARGV[1]) - 1)
local result = {}
for _, key in ipairs(entries) do
    if redis.call('EXISTS', key) == 1 then
        local metadata = redis.call('HMGET', key .. ':meta',
            'details', 'hits', 'created_at', 'last_used_at')
        if metadata[1] then
            table.insert(result, {key, metadata})
        end
    elseif redis.call('EXISTS', key .. ':candidates', key .. ':reservations') == 0 then
        redis.call('ZREM', KEYS[1], key)
        redis.call('DEL', key .. ':meta')
    end
end
return result
"""

_PREVIEW = """
local len = redis.call('STRLEN', KEYS[1])
if len == 0 or len > tonumber(ARGV[1]) then return false end
local details = redis.call('HGET', KEYS[2], 'details')
if not details then return false end
return {redis.call('GET', KEYS[1]), details}
"""


class RedisCacheBackend:
    # Older namespaces allowed unreserved writers. Keep rolling deployments
    # isolated so they cannot bypass reservations. Old keys expire normally.
    def __init__(self, client: Redis, *, prefix: str = "dograh:tts:v3"):
        self.client = client
        self.prefix = prefix

    def _index(self, organization_id: int) -> str:
        # All keys for an organization share a Redis Cluster hash slot.
        return f"{self.prefix}:{{{organization_id}}}:entries"

    def _entry_key(self, organization_id: int, digest: str) -> str:
        return f"{self.prefix}:{{{organization_id}}}:{digest}"

    def _key(self, request: SynthesisRequest) -> str:
        return self._entry_key(request.organization_id, request.digest)

    def _keys(self, organization_id: int, digest: str) -> tuple[str, ...]:
        key = self._entry_key(organization_id, digest)
        return (
            self._index(organization_id),
            key,
            f"{key}:meta",
            f"{key}:candidates",
            f"{key}:reservations",
        )

    async def get(
        self, request: SynthesisRequest, max_bytes: int, ttl_seconds: int
    ) -> bytes | None:
        return await self.client.eval(
            _GET,
            5,
            *self._keys(request.organization_id, request.digest),
            max_bytes,
            ttl_seconds,
        )

    async def put(
        self,
        request: SynthesisRequest,
        value: bytes,
        policy: CachePolicy,
        *,
        token: str,
    ) -> tuple[int, int]:
        """Collect a completed take; atomically publish the median at three."""
        audio_bytes = max(0, len(value) - 4 - int.from_bytes(value[:4], "big"))
        duration_seconds = audio_bytes / (request.sample_rate * request.channels * 2)
        details = json.dumps(
            {
                "provider": request.provider,
                "text_preview": request.text_preview,
                "model": request.model,
                "voice_id": request.voice_id,
                "sample_rate": request.sample_rate,
                "channels": request.channels,
                "duration_seconds": duration_seconds,
            }
        )
        count, evicted = await self.client.eval(
            _PUT,
            5,
            *self._keys(request.organization_id, request.digest),
            value,
            policy.ttl_seconds,
            details,
            token,
            duration_seconds,
        )
        return int(count), int(evicted)

    async def claim_candidate(
        self, request: SynthesisRequest, token: str, policy: CachePolicy
    ) -> tuple[bool, int]:
        claimed, evicted = await self.client.eval(
            _CLAIM,
            5,
            *self._keys(request.organization_id, request.digest),
            token,
            policy.ttl_seconds,
            policy.max_entries_per_org,
            policy.max_evictions_per_put,
            policy.reservation_ttl_seconds,
        )
        return bool(claimed), int(evicted)

    async def release_candidate(self, request: SynthesisRequest, token: str) -> None:
        await self.client.eval(
            _RELEASE, 5, *self._keys(request.organization_id, request.digest), token
        )

    async def delete_if_value(self, request: SynthesisRequest, value: bytes) -> bool:
        return bool(
            await self.client.eval(
                _DELETE,
                5,
                *self._keys(request.organization_id, request.digest),
                value,
            )
        )

    async def delete_entry(self, organization_id: int, digest: str) -> bool:
        return bool(
            await self.client.eval(
                _DELETE,
                5,
                *self._keys(organization_id, digest),
            )
        )

    async def list_entries(self, organization_id: int, limit: int) -> list[dict]:
        """Read metadata without transferring PCM or recording cache hits."""
        rows = await self.client.eval(_LIST, 1, self._index(organization_id), limit)
        entries = []
        for key, (details, hits, created_at, last_used_at) in rows:
            try:
                entries.append(
                    {
                        **json.loads(details),
                        "id": key.decode().rsplit(":", 1)[-1],
                        "hit_count": int(hits or 0),
                        "created_at": float(created_at),
                        "last_used_at": float(last_used_at),
                    }
                )
            except (ValueError, TypeError):
                # Malformed display metadata must not hide the other entries.
                continue
        return entries

    async def preview(self, organization_id: int, digest: str, max_bytes: int):
        """Read audio without renewing idle expiry or counting a synthesis hit."""
        key = self._entry_key(organization_id, digest)
        return await self.client.eval(_PREVIEW, 2, key, f"{key}:meta", max_bytes)

    async def invalidate_organization(self, organization_id: int) -> int:
        return await self.client.eval(_INVALIDATE, 1, self._index(organization_id))

    async def close(self) -> None:
        await self.client.aclose()
