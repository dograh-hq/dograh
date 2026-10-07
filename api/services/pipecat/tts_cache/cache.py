"""Bounded cache operations and capture memory shared by a worker's calls."""

import asyncio
import time
import uuid
from typing import Protocol

from loguru import logger
from opentelemetry import trace

from api.services.observability.metrics import get_runtime
from api.services.pipecat.tts_cache.models import (
    CachedSpeech,
    CachePolicy,
    SynthesisRequest,
)


class CacheBackend(Protocol):
    async def get(
        self, request: SynthesisRequest, max_bytes: int, ttl_seconds: int
    ) -> bytes | None: ...

    async def put(
        self,
        request: SynthesisRequest,
        value: bytes,
        policy: CachePolicy,
        *,
        token: str,
    ) -> tuple[int, int]:
        """Return accepted pool size (0 if ignored, 3 if promoted) and evictions."""
        ...

    async def claim_candidate(
        self, request: SynthesisRequest, token: str, policy: CachePolicy
    ) -> tuple[bool, int]: ...

    async def release_candidate(
        self, request: SynthesisRequest, token: str
    ) -> None: ...

    async def invalidate_organization(self, organization_id: int) -> int: ...

    async def delete_if_value(
        self, request: SynthesisRequest, value: bytes
    ) -> bool: ...

    async def close(self) -> None: ...


class SpeechCache:
    def __init__(self, backend: CacheBackend, policy: CachePolicy):
        self.backend = backend
        self.policy = policy
        self.capture_bytes = 0
        self._unavailable_until = 0.0

    def record(self, provider: str, result: str, *, seconds: float | None = None):
        logger.debug("TTS cache provider={} result={}", provider, result)
        trace.get_current_span().add_event(
            "tts.cache", {"cache.provider": provider, "cache.result": result}
        )
        runtime = get_runtime()
        if runtime:
            attrs = {"provider": provider, "result": result}
            runtime.tts_cache_events.add(1, attrs)
            if seconds is not None:
                runtime.tts_cache_latency.record(seconds, attrs)

    async def get(self, request: SynthesisRequest) -> CachedSpeech | None:
        if time.monotonic() < self._unavailable_until:
            self.record(request.provider, "cooldown")
            return None
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.policy.operation_timeout_seconds):
                value = await self.backend.get(
                    request,
                    self.policy.max_entry_bytes + 1028,
                    self.policy.ttl_seconds,
                )
        except Exception:  # noqa: BLE001 - Cache availability must not stop a call.
            self._unavailable_until = (
                time.monotonic() + self.policy.failure_cooldown_seconds
            )
            self.record(
                request.provider, "read_error", seconds=time.monotonic() - started
            )
            return None
        try:
            speech = (
                CachedSpeech.decode(value, request, self.policy)
                if value is not None
                else None
            )
        except (ValueError, TypeError, KeyError):
            self.record(request.provider, "invalid_entry")
            # Another caller may have repaired this key since our read. Only
            # remove the value we rejected, leaving any replacement intact.
            try:
                async with asyncio.timeout(self.policy.operation_timeout_seconds):
                    await self.backend.delete_if_value(request, value)
            except Exception:  # noqa: BLE001 - Repair must not stop synthesis.
                self.record(request.provider, "delete_error")
            return None
        self.record(
            request.provider,
            "hit" if speech else "miss",
            seconds=time.monotonic() - started,
        )
        return speech

    async def claim_candidate(self, request: SynthesisRequest) -> str | None:
        """Choose contributors before synthesis, independently of completion speed."""
        if time.monotonic() < self._unavailable_until:
            return None
        token = uuid.uuid4().hex
        try:
            async with asyncio.timeout(self.policy.operation_timeout_seconds):
                claimed, evicted = await self.backend.claim_candidate(
                    request, token, self.policy
                )
        except Exception:  # noqa: BLE001 - A failed claim still permits live audio.
            self._unavailable_until = (
                time.monotonic() + self.policy.failure_cooldown_seconds
            )
            self.record(request.provider, "claim_error")
            return None
        self.record(request.provider, "reserved" if claimed else "not_reserved")
        if evicted and (runtime := get_runtime()):
            runtime.tts_cache_evictions.add(evicted, {"provider": request.provider})
        return token if claimed else None

    async def release_candidate(self, request: SynthesisRequest, token: str) -> None:
        # Try cleanup even during cooldown. Lost/uncertain claims expire in Redis.
        try:
            async with asyncio.timeout(self.policy.operation_timeout_seconds):
                await self.backend.release_candidate(request, token)
        except Exception:  # noqa: BLE001 - Cleanup must not stop synthesis.
            self.record(request.provider, "release_error")

    async def put(
        self, request: SynthesisRequest, speech: CachedSpeech, *, token: str
    ) -> None:
        if time.monotonic() < self._unavailable_until:
            return
        started = time.monotonic()
        try:
            speech.validate(request, self.policy)
            value = speech.encode()
            async with asyncio.timeout(self.policy.operation_timeout_seconds):
                count, evicted = await self.backend.put(
                    request, value, self.policy, token=token
                )
            result = {0: "not_admitted", 1: "candidate", 2: "candidate", 3: "stored"}[
                count
            ]
        except Exception:  # noqa: BLE001 - Cache availability must not stop a call.
            self._unavailable_until = (
                time.monotonic() + self.policy.failure_cooldown_seconds
            )
            self.record(
                request.provider, "write_error", seconds=time.monotonic() - started
            )
            return
        self.record(
            request.provider,
            result,
            seconds=time.monotonic() - started,
        )
        if runtime := get_runtime():
            attrs = {"provider": request.provider}
            if evicted:
                # A sustained eviction rate means the working set no longer fits
                # and the provider is being called for entries the cache held.
                runtime.tts_cache_evictions.add(evicted, attrs)
            if count:
                runtime.tts_cache_audio_bytes.record(len(speech.audio), attrs)

    def reserve(self, count: int) -> bool:
        if self.capture_bytes + count > self.policy.max_capture_bytes:
            return False
        self.capture_bytes += count
        return True

    def release(self, count: int) -> None:
        self.capture_bytes -= count

    async def invalidate_organization(self, organization_id: int) -> int:
        if type(organization_id) is not int or organization_id <= 0:
            raise ValueError("Organization ID is required")
        async with asyncio.timeout(self.policy.operation_timeout_seconds):
            return await self.backend.invalidate_organization(organization_id)

    async def close(self) -> None:
        await self.backend.close()
