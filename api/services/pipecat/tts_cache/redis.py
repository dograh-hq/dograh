"""Atomic, organization-scoped Redis admission with fixed entry expiration."""

from redis.asyncio import Redis

from api.services.pipecat.tts_cache.models import CachePolicy, SynthesisRequest

_GET = """
if redis.call('STRLEN', KEYS[1]) > tonumber(ARGV[1]) then return false end
return redis.call('GET', KEYS[1])
"""

_PUT = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) then return 0 end
redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[2])
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), KEYS[2])
local last = redis.call('ZRANGE', KEYS[1], -1, -1, 'WITHSCORES')
redis.call('EXPIREAT', KEYS[1], math.ceil(tonumber(last[2])) + 1)
return 1
"""

_INVALIDATE = """
local entries = redis.call('ZRANGE', KEYS[1], 0, -1)
local removed = 0
for _, key in ipairs(entries) do removed = removed + redis.call('DEL', key) end
redis.call('DEL', KEYS[1])
return removed
"""


class RedisCacheBackend:
    def __init__(self, client: Redis, *, prefix: str = "dograh:tts:v1"):
        self.client = client
        self.prefix = prefix

    def _index(self, organization_id: int) -> str:
        # All keys for an organization share a Redis Cluster hash slot.
        return f"{self.prefix}:{{{organization_id}}}:entries"

    def _key(self, request: SynthesisRequest) -> str:
        return f"{self.prefix}:{{{request.organization_id}}}:{request.digest}"

    async def get(self, request: SynthesisRequest, max_bytes: int) -> bytes | None:
        return await self.client.eval(_GET, 1, self._key(request), max_bytes)

    async def put(
        self, request: SynthesisRequest, value: bytes, policy: CachePolicy
    ) -> bool:
        return bool(
            await self.client.eval(
                _PUT,
                2,
                self._index(request.organization_id),
                self._key(request),
                value,
                policy.ttl_seconds,
                policy.max_entries_per_org,
            )
        )

    async def invalidate_organization(self, organization_id: int) -> int:
        return await self.client.eval(_INVALIDATE, 1, self._index(organization_id))

    async def close(self) -> None:
        await self.client.aclose()
