"""Which workflow run an ARI channel belongs to.

Origination happens wherever the call was dispatched from - a campaign worker,
an API request - while ARI's events arrive in the ari_manager process. The two
share nothing but Redis, so the mapping lives there.

It is written the moment the channel exists rather than when the channel enters
Stasis, and that timing is the whole point. A call that is rejected, busy or
simply never answered is created, destroyed, and never enters Stasis at all, so
a mapping written at StasisStart never exists for exactly the calls that need
cleaning up. Those are also the majority of calls on a real dialer, and each
one was holding a concurrency slot and a caller ID until the stale sweep
reclaimed them 20 minutes later.
"""

from __future__ import annotations

import redis.asyncio as aioredis
from loguru import logger

from api.constants import REDIS_URL

CHANNEL_KEY_PREFIX = "ari:channel:"

# Long enough to outlive any call, short enough that a mapping whose teardown
# never ran cannot accumulate. Cleanup deletes the key; this is the backstop.
CHANNEL_KEY_TTL = 3600

_redis_client: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = await aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


async def register_channel(channel_id: str, workflow_run_id: int | str) -> None:
    """Record that ``channel_id`` belongs to ``workflow_run_id``.

    Failure is logged rather than raised: losing the mapping costs a delayed
    cleanup, whereas failing the call costs the call.
    """
    if not channel_id:
        return
    try:
        client = await _get_redis()
        await client.set(
            f"{CHANNEL_KEY_PREFIX}{channel_id}",
            str(workflow_run_id),
            ex=CHANNEL_KEY_TTL,
        )
    except Exception as e:
        logger.warning(
            f"[ARI] Could not register channel {channel_id} for workflow run "
            f"{workflow_run_id}: {e}. Cleanup falls back to the stale sweep."
        )
