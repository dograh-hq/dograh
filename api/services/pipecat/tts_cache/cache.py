"""Bounded cache operations and capture memory shared by a worker's calls."""

import asyncio
import time
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
    async def get(self, request: SynthesisRequest, max_bytes: int) -> bytes | None: ...

    async def put(
        self, request: SynthesisRequest, value: bytes, policy: CachePolicy
    ) -> bool: ...

    async def invalidate_organization(self, organization_id: int) -> int: ...

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
                    request, self.policy.max_entry_bytes + 1028
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
            return None
        self.record(
            request.provider,
            "hit" if speech else "miss",
            seconds=time.monotonic() - started,
        )
        return speech

    async def put(self, request: SynthesisRequest, speech: CachedSpeech) -> None:
        if time.monotonic() < self._unavailable_until:
            return
        started = time.monotonic()
        try:
            speech.validate(request, self.policy)
            value = speech.encode()
            async with asyncio.timeout(self.policy.operation_timeout_seconds):
                stored = await self.backend.put(request, value, self.policy)
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
            "stored" if stored else "not_admitted",
            seconds=time.monotonic() - started,
        )
        if stored and (runtime := get_runtime()):
            runtime.tts_cache_audio_bytes.record(
                len(speech.audio), {"provider": request.provider}
            )

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
