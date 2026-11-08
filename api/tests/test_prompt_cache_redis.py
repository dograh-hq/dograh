"""Opt-in Redis integration checks; writes only UUID-scoped cache keys.

Run with DOGRAH_TEST_PROMPT_CACHE_REDIS=1 and api/.env.test sourced.
Provider calls are simulated, so these checks incur no Google charges.
"""

import asyncio
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlparse

import pytest
from google.genai.types import CachedContent, CachedContentUsageMetadata
from redis.asyncio import Redis

from api.services.pipecat.prompt_cache import CacheSpec, PromptCacheRegistry

pytestmark = pytest.mark.skipif(
    os.getenv("DOGRAH_TEST_PROMPT_CACHE_REDIS") != "1",
    reason="Opt-in local Redis integration test",
)


@pytest.mark.asyncio
async def test_cross_worker_single_flight_refresh_and_rejection_fencing():
    from api.constants import REDIS_URL

    assert urlparse(REDIS_URL).hostname in {"localhost", "127.0.0.1", "::1"}
    redis = Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=2)
    spec = CacheSpec(
        1,
        1,
        "test-node",
        str(uuid.uuid4()),
        "gemini-3-flash-preview",
        "static prompt",
        [],
    )
    resources = []

    async def listing(**kwargs):
        async def values():
            for value in resources:
                yield value

        return values()

    async def create(**kwargs):
        # Keep the provider busy while other workers compete for the lease.
        await asyncio.sleep(0.03)
        resource = CachedContent(
            name=f"cachedContents/{len(resources)}",
            display_name=spec.display_name,
            expire_time=datetime.now(UTC) + timedelta(hours=1),
            usage_metadata=CachedContentUsageMetadata(total_token_count=6000),
        )
        resources.append(resource)
        return resource

    async def refresh(**kwargs):
        return resources[-1].model_copy(
            update={"expire_time": datetime.now(UTC) + timedelta(hours=1)}
        )

    caches = SimpleNamespace(
        list=AsyncMock(side_effect=listing),
        create=AsyncMock(side_effect=create),
        update=AsyncMock(side_effect=refresh),
    )
    client = SimpleNamespace(aio=SimpleNamespace(caches=caches))
    workers = [PromptCacheRegistry(redis) for _ in range(4)]
    try:
        for index in range(60):
            workers[index % 4].prewarm(spec, client)
        await asyncio.gather(
            *(task for worker in workers for task in worker.tasks.values())
        )
        assert caches.create.await_count == 1
        entries = await asyncio.gather(*(worker.lookup(spec) for worker in workers))
        assert all(entry and entry.name == resources[0].name for entry in entries)

        await redis.hset(spec.key, "expires", str(time.time() + 100))
        for worker in workers:
            worker.local.clear()
        await asyncio.gather(*(worker._ensure(spec, client) for worker in workers))
        assert caches.update.await_count == 1

        old = entries[0]
        started, release = asyncio.Event(), asyncio.Event()

        async def delayed_refresh(**kwargs):
            started.set()
            await asyncio.wait_for(release.wait(), timeout=2)
            return await refresh(**kwargs)

        caches.update.side_effect = delayed_refresh
        await redis.hset(spec.key, "expires", str(time.time() + 100))
        workers[0].local.clear()
        refresh_task = asyncio.create_task(workers[0]._ensure(spec, client))
        await asyncio.wait_for(started.wait(), timeout=2)
        await workers[0].invalidate(spec, old)
        release.set()
        await asyncio.wait_for(refresh_task, timeout=2)
        # Exercise _ensure's real store path after a racing rejection.
        assert not await redis.exists(spec.key)
        for worker in workers:
            worker.local.clear()
        await workers[0]._ensure(spec, client)
        assert caches.create.await_count == 2
        newest = await workers[0].lookup(spec)
        assert newest.name != old.name
        # A delayed rejection from another worker cannot delete its replacement.
        await workers[1].invalidate(spec, old)
        workers[1].local.clear()
        assert (await workers[1].lookup(spec)).name == newest.name
    finally:
        tasks = [task for worker in workers for task in worker.tasks.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        keys = [key async for key in redis.scan_iter(match=spec.key + "*")]
        if keys:
            await redis.delete(*keys)
        await redis.delete(spec.model_key)
        await redis.aclose()
