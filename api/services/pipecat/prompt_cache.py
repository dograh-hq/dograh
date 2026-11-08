"""Worker-shared Gemini cache registry. Provider work never blocks a call.

Only static, provider-shaped instructions and tools enter a cache. Redis owns
creation/refresh leases; an unavailable registry always means inline inference.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from google.genai import types
from loguru import logger
from opentelemetry import metrics
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from api.constants import is_prompt_cache_disabled as cache_disabled

TTL_SECONDS = 3600
EXPIRY_MARGIN_SECONDS = 30
_counter = metrics.get_meter(__name__).create_counter("dograh.prompt_cache.operations")
_RELEASE = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"
_INVALIDATE = """
redis.call('SET', KEYS[2], '1', 'EX', ARGV[2])
if redis.call('HGET', KEYS[1], 'name') == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""
_STORE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
if redis.call('EXISTS', KEYS[3]) > 0 then return 0 end
redis.call('HSET', KEYS[2], 'name', ARGV[2], 'expires', ARGV[3], 'tokens', ARGV[4], 'created_by', ARGV[5])
redis.call('EXPIRE', KEYS[2], ARGV[6])
return 1
"""


def count_cache_event(operation: str) -> None:
    _counter.add(1, {"operation": operation})


@dataclass(frozen=True)
class CacheSpec:
    organization_id: int
    workflow_id: int
    node_id: str
    scope: str
    model: str
    system_instruction: str
    tools: list
    tool_config: Any = None

    @property
    def key(self) -> str:
        payload = types.CreateCachedContentConfig(
            system_instruction=self.system_instruction,
            tools=self.tools or None,
            tool_config=self.tool_config,
        ).model_dump(mode="json", exclude_none=True)
        encoded = json.dumps(
            [
                self.organization_id,
                self.workflow_id,
                self.node_id,
                self.scope,
                self.model,
                payload,
            ],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return "dograh:prompt-cache:" + hashlib.sha256(encoded.encode()).hexdigest()

    @property
    def display_name(self) -> str:
        # Keep arbitrary editor node IDs out of the provider name/length budget.
        node = hashlib.sha256(self.node_id.encode()).hexdigest()[:8]
        return f"dograh-{self.organization_id}-{self.workflow_id}-{node}-{self.key.rsplit(':', 1)[-1][:24]}"

    @property
    def model_key(self) -> str:
        scope = json.dumps([self.organization_id, self.scope, self.model])
        return (
            "dograh:prompt-cache:unsupported:"
            + hashlib.sha256(scope.encode()).hexdigest()
        )


@dataclass(frozen=True)
class CacheEntry:
    name: str
    expires: float
    tokens: int

    @property
    def live(self) -> bool:
        return self.expires > time.time() + EXPIRY_MARGIN_SECONDS


def rejected_cached_content(error: Exception) -> bool:
    code = getattr(error, "code", None)
    message = str(getattr(error, "message", error)).lower()
    return code in (400, 404) and any(
        name in message
        for name in (
            "cachedcontent",
            "cached_content",
            "cached content",
            "cache resource",
        )
    )


class PromptCacheRegistry:
    def __init__(self, redis: Redis):
        self.redis = redis
        self.local: OrderedDict[str, tuple[CacheEntry, float]] = OrderedDict()
        self.tasks: dict[str, asyncio.Task] = {}
        self._closed = False

    def _remember(self, key: str, entry: CacheEntry) -> None:
        self.local[key] = (entry, time.monotonic() + 5)
        self.local.move_to_end(key)
        while len(self.local) > 1024:
            self.local.popitem(last=False)

    async def lookup(self, spec: CacheSpec) -> CacheEntry | None:
        if cache_disabled():
            return None
        key = spec.key
        found = self.local.get(key)
        if found and found[1] > time.monotonic() and found[0].live:
            return found[0]
        self.local.pop(key, None)
        try:
            async with asyncio.timeout(0.1):
                row = await self.redis.hgetall(key)
            if row:
                entry = CacheEntry(
                    row["name"], float(row["expires"]), int(row["tokens"])
                )
                if entry.live:
                    self._remember(key, entry)
                    return entry
        except Exception:
            # Redis failures must not expose credentials through exception text.
            count_cache_event("registry_error")
        return None

    def prewarm(
        self, spec: CacheSpec, client, *, credentials=None, recreate=False
    ) -> None:
        if cache_disabled() or self._closed:
            return
        if spec.key in self.tasks:
            if recreate:
                self.tasks[spec.key].add_done_callback(
                    lambda _: self.prewarm(spec, client, credentials=credentials)
                )
            return
        task = asyncio.create_task(
            self._ensure(spec, client, credentials=credentials),
            name=f"prompt-cache:{spec.display_name}",
        )
        self.tasks[spec.key] = task
        task.add_done_callback(lambda done: self.tasks.pop(spec.key, None))

    async def invalidate(self, spec: CacheSpec, entry: CacheEntry) -> None:
        self.local.pop(spec.key, None)
        try:
            async with asyncio.timeout(0.1):
                await self.redis.eval(
                    _INVALIDATE,
                    2,
                    spec.key,
                    spec.key + ":rejected:" + entry.name.rsplit("/", 1)[-1],
                    entry.name,
                    TTL_SECONDS,
                )
        except Exception:
            count_cache_event("registry_error")

    async def status(self, spec: CacheSpec) -> tuple[str, int | None]:
        if cache_disabled():
            return "disabled", None
        entry = await self.lookup(spec)
        if entry:
            return "ready", entry.tokens
        try:
            async with asyncio.timeout(0.1):
                if await self.redis.exists(spec.model_key):
                    return "unsupported", None
                reason = await self.redis.get(spec.key + ":backoff")
                return reason or "not_ready", None
        except Exception:
            return "registry_unavailable", None

    async def _ensure(self, spec: CacheSpec, client, *, credentials=None) -> None:
        key, lease = spec.key, uuid.uuid4().hex
        locked = False
        try:
            async with asyncio.timeout(25):
                if cache_disabled() or await self.redis.exists(
                    key + ":backoff", spec.model_key
                ):
                    return
                entry = await self.lookup(spec)
                if entry and entry.expires > time.time() + TTL_SECONDS / 3:
                    return
                locked = bool(
                    await self.redis.set(key + ":lock", lease, ex=30, nx=True)
                )
                if not locked:
                    return
                # Another worker may have published a cache while we waited.
                self.local.pop(key, None)
                entry = await self.lookup(spec)
                if entry and entry.expires > time.time() + TTL_SECONDS / 3:
                    return
                if credentials is not None:
                    from api.services.pipecat.google_credentials import (
                        prepare_google_credentials,
                    )

                    await prepare_google_credentials(credentials)
                operation = "refresh" if entry else "create"
                if entry:
                    try:
                        resource = await client.aio.caches.update(
                            name=entry.name,
                            config=types.UpdateCachedContentConfig(
                                ttl=f"{TTL_SECONDS}s"
                            ),
                        )
                    except Exception as error:
                        if not rejected_cached_content(error):
                            raise
                        await self.invalidate(spec, entry)
                        entry, operation = None, "create"
                if not entry:
                    # Recover after a Redis eviction/restart. Google display names
                    # aren't unique, so the distributed lease also covers listing.
                    resource = None
                    async for candidate in await client.aio.caches.list(
                        config={"page_size": 100}
                    ):
                        if (
                            candidate.display_name != spec.display_name
                            or not candidate.expire_time
                        ):
                            continue
                        if (
                            candidate.expire_time.timestamp()
                            <= time.time() + EXPIRY_MARGIN_SECONDS
                        ):
                            continue
                        if await self.redis.exists(
                            key + ":rejected:" + candidate.name.rsplit("/", 1)[-1]
                        ):
                            continue
                        resource = candidate
                        break
                    if resource is None:
                        resource = await client.aio.caches.create(
                            model=spec.model,
                            config=types.CreateCachedContentConfig(
                                display_name=spec.display_name,
                                system_instruction=spec.system_instruction,
                                tools=spec.tools or None,
                                tool_config=spec.tool_config,
                                ttl=f"{TTL_SECONDS}s",
                            ),
                        )
                if not resource.name or not resource.expire_time:
                    raise ValueError("Incomplete cache response")
                tokens = getattr(resource.usage_metadata, "total_token_count", 0) or 0
                entry = CacheEntry(
                    resource.name, resource.expire_time.timestamp(), tokens
                )
                stored = await self.redis.eval(
                    _STORE,
                    3,
                    key + ":lock",
                    key,
                    key + ":rejected:" + entry.name.rsplit("/", 1)[-1],
                    lease,
                    entry.name,
                    str(entry.expires),
                    str(entry.tokens),
                    str(os.getpid()),
                    max(1, int(entry.expires - time.time())),
                )
                if stored:
                    self._remember(key, entry)
                    count_cache_event(operation)
                    logger.bind(
                        cache_key=key,
                        node_id=spec.node_id,
                        organization_id=spec.organization_id,
                    ).info("Prompt cache {}", operation)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = getattr(error, "code", None)
            message = str(getattr(error, "message", error)).lower()
            reason, delay, marker = "error", 60, key + ":backoff"
            if code == 400 and any(
                word in message
                for word in ("too small", "minimum token", "too few", "min_total_token")
            ):
                reason, delay = "too_small", 3600
            elif code in (400, 404) and any(
                word in message
                for word in ("not supported", "unsupported", "does not support")
            ):
                reason, delay, marker = "unsupported", 3600, spec.model_key
            if "entry" in locals() and entry and rejected_cached_content(error):
                await self.invalidate(spec, entry)
            try:
                async with asyncio.timeout(0.1):
                    await self.redis.set(marker, reason, ex=delay)
            except Exception:
                pass
            count_cache_event(reason)
            logger.bind(
                cache_key=key, node_id=spec.node_id, reason=reason, status=code
            ).warning("Prompt cache unavailable; using inline requests")
        finally:
            if locked:
                try:
                    async with asyncio.timeout(0.1):
                        await self.redis.eval(_RELEASE, 1, key + ":lock", lease)
                except Exception:
                    pass

    async def close(self) -> None:
        self._closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.redis.aclose()


_registry: PromptCacheRegistry | None = None


def get_prompt_cache_registry() -> PromptCacheRegistry:
    global _registry
    if _registry is None:
        from api.constants import REDIS_URL

        _registry = PromptCacheRegistry(
            Redis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_timeout=0.1,
                socket_connect_timeout=0.1,
                retry=Retry(NoBackoff(), 0),
                max_connections=32,
            )
        )
    return _registry


async def close_prompt_cache() -> None:
    global _registry
    registry, _registry = _registry, None
    if registry:
        await registry.close()
