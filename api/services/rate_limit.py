"""Redis fixed-window rate limiting for abuse-prone endpoints.

Targets the brute-force / spam surface the security audit flagged: auth login &
signup (signup also provisions a VoiceLink client, so spamming it is costly) and
the public X-API-Key call-trigger surface.

Backed by the existing arq Redis pool (`get_arq_redis`) via INCR + EXPIRE — no new
connection pool. FAIL-OPEN: if Redis is unavailable the request is allowed (a brief
Redis blip must not lock every user out), with a warning logged.
"""

from __future__ import annotations

import redis.asyncio as aioredis
from fastapi import HTTPException, Request
from loguru import logger

from api.constants import REDIS_URL

# Dedicated, lazily-created pool — decoupled from the arq task pool so importing
# this module doesn't drag in the background-task chain.
_pool: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
    return _pool


def client_ip(request: Request) -> str:
    """Real client IP behind the edge reverse proxy.

    Caddy/nginx (the edge, no trusted_proxies configured) APPEND the actual TCP
    peer as the LAST X-Forwarded-For entry; any entries to the left are
    client-supplied and spoofable. So trust the RIGHTMOST entry — otherwise a
    brute-forcer could rotate a fake leftmost IP to get a fresh bucket per request.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


async def enforce_rate_limit(
    *, bucket: str, identity: str, limit: int, window_seconds: int
) -> None:
    """Raise HTTP 429 when `identity` exceeds `limit` requests per window in `bucket`."""
    if limit <= 0:  # 0/negative disables the limit
        return
    key = f"rl:{bucket}:{identity}"
    try:
        redis = await _get_redis()
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window_seconds)
        if count > limit:
            ttl = await redis.ttl(key)
            retry_after = ttl if ttl and ttl > 0 else window_seconds
            logger.warning(
                f"Rate limit hit: bucket={bucket} identity={identity} "
                f"count={count}/{limit} per {window_seconds}s"
            )
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please slow down and try again shortly.",
                headers={"Retry-After": str(retry_after)},
            )
    except HTTPException:
        raise
    except Exception as exc:  # Redis down / pool error — never block legit traffic
        logger.warning(f"Rate limit check unavailable, allowing request: {exc}")


def rate_limit_ip(bucket: str, limit: int, window_seconds: int = 60):
    """FastAPI dependency: limit by client IP."""

    async def _dep(request: Request) -> None:
        await enforce_rate_limit(
            bucket=bucket,
            identity=client_ip(request),
            limit=limit,
            window_seconds=window_seconds,
        )

    return _dep
