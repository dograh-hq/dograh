"""Which workflow run an ARI channel belongs to.

Origination happens wherever the call was dispatched from - a campaign worker,
an API request - while ARI's events arrive in the ari_manager process. The two
share nothing but Redis, so the mapping lives there.

The provider assigns a channel ID and registers it before sending the originate
request. A rejected or busy call can be destroyed before the HTTP response and
never enter Stasis, so registering at either of those later points can miss the
event that releases its concurrency slot and finalizes its workflow run.

Explicit origination errors remove the mapping. Transport failures leave it
until destruction or expiry because Asterisk may have accepted the call.
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


async def register_channel(channel_id: str, workflow_run_id: int | str | None) -> None:
    """Record that ``channel_id`` belongs to ``workflow_run_id``.

    Failure is logged rather than raised: losing the mapping costs a delayed
    cleanup, whereas failing the call costs the call.
    """
    if not channel_id or workflow_run_id is None:
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


async def unregister_channel(channel_id: str) -> None:
    """Remove a rejected origination's mapping without masking its error."""
    try:
        client = await _get_redis()
        await client.delete(f"{CHANNEL_KEY_PREFIX}{channel_id}")
    except Exception as e:
        logger.warning(
            f"[ARI] Could not unregister channel {channel_id}: {e}. "
            "The mapping will expire."
        )
