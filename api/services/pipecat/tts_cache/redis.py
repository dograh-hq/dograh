"""Organization-scoped Redis cache with atomic LRU admission and idle expiry."""

import json

from redis.asyncio import Redis

from api.services.pipecat.tts_cache.models import CachePolicy, SynthesisRequest

_GET = """
local len = redis.call('STRLEN', KEYS[2])
if len == 0 or len > tonumber(ARGV[1]) then
    redis.call('DEL', KEYS[2], KEYS[3])
    redis.call('ZREM', KEYS[1], KEYS[2])
    return false
end
local value = redis.call('GET', KEYS[2])
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local ttl = math.ceil(tonumber(ARGV[2]))
redis.call('EXPIRE', KEYS[2], ttl)
redis.call('ZADD', KEYS[1], now, KEYS[2])
redis.call('EXPIRE', KEYS[1], ttl + 1)
if redis.call('HEXISTS', KEYS[3], 'details') == 1 then
    redis.call('HINCRBY', KEYS[3], 'hits', 1)
    redis.call('HSET', KEYS[3], 'last_used_at', tostring(now))
    redis.call('EXPIRE', KEYS[3], ttl)
end
return value
"""

_PUT = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local ttl = math.ceil(tonumber(ARGV[2]))
local cap = tonumber(ARGV[3])
local drain = tonumber(ARGV[4])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - ttl)
-- A concurrent synthesis is not a cache hit and must not prolong an existing
-- value's lifetime. The reader removes invalid values before regenerating.
if redis.call('EXISTS', KEYS[2]) == 1 then return {0, 0} end
local evicted = 0
-- Bound work when a reduced capacity requires more than one eviction.
while evicted < drain and redis.call('ZCARD', KEYS[1]) >= cap do
    local victim = redis.call('ZPOPMIN', KEYS[1])
    if victim[1] == nil then break end
    redis.call('DEL', victim[1], victim[1] .. ':meta')
    evicted = evicted + 1
end
if redis.call('ZCARD', KEYS[1]) >= cap then return {0, evicted} end
redis.call('SET', KEYS[2], ARGV[1], 'EX', ttl)
redis.call('HSET', KEYS[3], 'details', ARGV[5], 'hits', 0,
    'created_at', tostring(now), 'last_used_at', tostring(now))
redis.call('EXPIRE', KEYS[3], ttl)
redis.call('ZADD', KEYS[1], now, KEYS[2])
redis.call('EXPIRE', KEYS[1], ttl + 1)
return {1, evicted}
"""

_DELETE = """
if #ARGV > 0 and redis.call('GET', KEYS[2]) ~= ARGV[1] then return 0 end
local removed = redis.call('DEL', KEYS[2])
redis.call('DEL', KEYS[3])
redis.call('ZREM', KEYS[1], KEYS[2])
return removed
"""

_INVALIDATE = """
local entries = redis.call('ZRANGE', KEYS[1], 0, -1)
local removed = 0
for _, key in ipairs(entries) do
    removed = removed + redis.call('DEL', key)
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
    else
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
    # Stop old cache writers and clear their v1 keys before deploying this index
    # format. Old scores represent expiry, whereas these represent last access.
    def __init__(self, client: Redis, *, prefix: str = "dograh:tts:v1"):
        self.client = client
        self.prefix = prefix

    def _index(self, organization_id: int) -> str:
        # All keys for an organization share a Redis Cluster hash slot.
        return f"{self.prefix}:{{{organization_id}}}:entries"

    def _entry_key(self, organization_id: int, digest: str) -> str:
        return f"{self.prefix}:{{{organization_id}}}:{digest}"

    def _key(self, request: SynthesisRequest) -> str:
        return self._entry_key(request.organization_id, request.digest)

    async def get(
        self, request: SynthesisRequest, max_bytes: int, ttl_seconds: int
    ) -> bytes | None:
        key = self._key(request)
        return await self.client.eval(
            _GET,
            3,
            self._index(request.organization_id),
            key,
            f"{key}:meta",
            max_bytes,
            ttl_seconds,
        )

    async def put(
        self, request: SynthesisRequest, value: bytes, policy: CachePolicy
    ) -> tuple[bool, int]:
        """Admit an entry and its bounded display metadata together."""
        key = self._key(request)
        audio_bytes = max(0, len(value) - 4 - int.from_bytes(value[:4], "big"))
        details = json.dumps(
            {
                "provider": request.provider,
                "text_preview": request.text_preview,
                "model": request.model,
                "voice_id": request.voice_id,
                "sample_rate": request.sample_rate,
                "channels": request.channels,
                "duration_seconds": audio_bytes
                / (request.sample_rate * request.channels * 2),
            }
        )
        stored, evicted = await self.client.eval(
            _PUT,
            3,
            self._index(request.organization_id),
            key,
            f"{key}:meta",
            value,
            policy.ttl_seconds,
            policy.max_entries_per_org,
            policy.max_evictions_per_put,
            details,
        )
        return bool(stored), int(evicted)

    async def delete_if_value(self, request: SynthesisRequest, value: bytes) -> bool:
        key = self._key(request)
        return bool(
            await self.client.eval(
                _DELETE,
                3,
                self._index(request.organization_id),
                key,
                f"{key}:meta",
                value,
            )
        )

    async def delete_entry(self, organization_id: int, digest: str) -> bool:
        key = self._entry_key(organization_id, digest)
        return bool(
            await self.client.eval(
                _DELETE,
                3,
                self._index(organization_id),
                key,
                f"{key}:meta",
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
