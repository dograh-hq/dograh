"""Worker-owned cache pool using the application's existing Redis instance."""

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from api.services.pipecat.tts_cache.cache import SpeechCache
from api.services.pipecat.tts_cache.models import CachePolicy
from api.services.pipecat.tts_cache.redis import RedisCacheBackend

_cache: SpeechCache | None = None


def get_speech_cache(
    organization_id: int | None, *, enabled: bool = False
) -> SpeechCache | None:
    global _cache
    if enabled is not True or type(organization_id) is not int or organization_id <= 0:
        return None
    if _cache is None:
        from api.constants import REDIS_URL

        policy = CachePolicy()
        client = Redis.from_url(
            REDIS_URL,
            decode_responses=False,
            socket_timeout=policy.operation_timeout_seconds,
            socket_connect_timeout=policy.operation_timeout_seconds,
            retry=Retry(NoBackoff(), 0),
            max_connections=32,
        )
        _cache = SpeechCache(RedisCacheBackend(client), policy)
    return _cache


async def close_speech_cache() -> None:
    global _cache
    cache, _cache = _cache, None
    if cache:
        await cache.close()
