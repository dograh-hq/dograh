"""Cache isolation, failure behavior, and MiniMax replay through real pipelines."""

import asyncio
import copy
import itertools
import json
import os
import time
import uuid
from dataclasses import replace
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp import web
from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    MetricsFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.metrics.metrics import TTSUsageMetricsData
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams
from pipecat.services.minimax.tts import MiniMaxTTSSettings
from pipecat.services.tts_service import TextAggregationMode
from pipecat.tests.mock_transport import MockOutputTransport
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.transports.base_transport import TransportParams
from redis.asyncio import Redis

from api.services.pipecat.minimax_tts import MiniMaxCachingTTSService
from api.services.pipecat.tts_cache.cache import SpeechCache
from api.services.pipecat.tts_cache.models import (
    CachedSpeech,
    CachePolicy,
    SynthesisRequest,
)
from api.services.pipecat.tts_cache.redis import RedisCacheBackend
from api.services.pipecat.tts_cache.runtime import close_speech_cache, get_speech_cache
from api.services.pipecat.tts_cache.synthesis import cached_synthesis

pytestmark = pytest.mark.asyncio
PCM = b"\x01\x02" * 256


def request_for(**overrides):
    args = {
        "organization_id": 1,
        "provider": "minimax",
        "adapter_version": 1,
        "endpoint": "https://api.minimax.io/v1/t2a_v2?GroupId=group",
        "credential": "test-credential",
        "sample_rate": 16000,
        "channels": 1,
        "payload": {
            "text": "Hello.",
            "model": "speech-2.8-hd",
            "voice_setting": {"voice_id": "voice", "speed": 1.0},
            "audio_setting": {"format": "pcm", "sample_rate": 16000, "channel": 1},
        },
    }
    args.update(overrides)
    return SynthesisRequest.from_payload(**args)


class MemoryBackend:
    """Small selection double; capacity, expiry and races use real Redis below."""

    def __init__(self):
        self.entries = {}
        self.candidates = {}
        self.reservations = {}
        self.reads = 0

    async def get(self, request, max_bytes, ttl_seconds):
        self.reads += 1
        key = (request.organization_id, request.digest)
        value = self.entries.get(key)
        return value

    async def claim_candidate(self, request, token, policy):
        key = (request.organization_id, request.digest)
        pending = self.reservations.setdefault(key, set())
        if key in self.entries or len(self.candidates.get(key, [])) + len(pending) >= 3:
            return False, 0
        pending.add(token)
        return True, 0

    async def release_candidate(self, request, token):
        key = (request.organization_id, request.digest)
        self.reservations.get(key, set()).discard(token)

    async def put(self, request, value, policy, *, token):
        key = (request.organization_id, request.digest)
        pending = self.reservations.get(key, set())
        if key in self.entries or token not in pending:
            return 0, 0
        pending.remove(token)
        pool = self.candidates.setdefault(key, [])
        speech = CachedSpeech.decode(value, request, policy)
        pool.append((len(speech.audio), value))
        count = len(pool)
        if count == 3:
            self.entries[key] = sorted(pool, key=lambda item: item[0])[1][1]
            del self.candidates[key]
        return count, 0

    async def close(self):
        pass

    async def delete_if_value(self, request, value):
        key = (request.organization_id, request.digest)
        if self.entries.get(key) != value:
            return False
        del self.entries[key]
        return True


@pytest.fixture
def cache():
    return SpeechCache(MemoryBackend(), CachePolicy())


async def collect_candidate(cache, request, speech):
    token = await cache.claim_candidate(request)
    if token is not None:
        try:
            await cache.put(request, speech, token=token)
        finally:
            await cache.release_candidate(request, token)


async def submit_to_backend(backend, request, value, policy):
    token = uuid.uuid4().hex
    claimed, evicted = await backend.claim_candidate(request, token, policy)
    if not claimed:
        return 0, evicted
    try:
        count, _ = await backend.put(request, value, policy, token=token)
        return count, evicted
    finally:
        await backend.release_candidate(request, token)


async def warm_cache(cache, request, speech):
    for _ in range(3):
        await collect_candidate(cache, request, speech)


@pytest.mark.parametrize(
    "changed",
    [
        {"organization_id": 2},
        {"credential": "rotated-credential"},
        {"endpoint": "https://api-uw.minimax.io/v1/t2a_v2?GroupId=group"},
        {"endpoint": "https://api.minimax.io/v1/t2a_v2?GroupId=another"},
        {"provider": "another"},
        {"adapter_version": 2},
        {"sample_rate": 24000},
    ],
)
async def test_account_and_adapter_identity_isolates_cache(cache, changed):
    await warm_cache(cache, request_for(), CachedSpeech(PCM, 16000))
    assert await cache.get(request_for(**changed)) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("text", "hello."),
        ("text", "Hello. "),
        ("text", "Hello.\n"),
        ("text", "Hello<#1#>."),
        ("model", "speech-2.8-turbo"),
        ("voice_setting", {"voice_id": "another"}),
        ("voice_setting", {"voice_id": "voice", "speed": 1.25}),
        ("language_boost", "English"),
        ("pronunciation_dict", {"tone": ["Hello/Hi"]}),
        ("audio_setting", {"format": "pcm", "sample_rate": 24000, "channel": 1}),
    ],
)
async def test_every_effective_payload_field_affects_identity(cache, field, value):
    payload = {
        "text": "Hello.",
        "model": "speech-2.8-hd",
        "voice_setting": {"voice_id": "voice", "speed": 1.0},
        "audio_setting": {"format": "pcm", "sample_rate": 16000, "channel": 1},
    }
    await warm_cache(cache, request_for(payload=payload), CachedSpeech(PCM, 16000))
    changed = copy.deepcopy(payload)
    changed[field] = value
    assert await cache.get(request_for(payload=changed)) is None


async def test_corruption_and_format_mismatch_are_misses(cache):
    req = request_for()
    await warm_cache(cache, req, CachedSpeech(PCM, 16000))
    key = (req.organization_id, req.digest)
    value = cache.backend.entries[key]
    for broken in (b"", b"xxxx", value[:-1], value[:-1] + b"x"):
        cache.backend.entries[key] = broken
        assert await cache.get(req) is None
    cache.backend.entries[key] = CachedSpeech(PCM, 24000).encode()
    assert await cache.get(req) is None


class AudioOnlyService:
    """Stand-in for a service whose run_tts output is only audio frames."""

    _push_text_frames = True
    _is_streaming_tokens = False


class WordTimestampService(AudioOnlyService):
    _push_text_frames = False


class TokenStreamingService(AudioOnlyService):
    _is_streaming_tokens = True


class UnreportedService:
    """A service that declares neither property is not assumed to be safe."""


def synthesize(
    cache, source, *, context_id="current", completed=lambda: True, service=None
):
    return cached_synthesis(
        service=service if service is not None else AudioOnlyService(),
        cache=cache,
        request=request_for(),
        context_id=context_id,
        source=source,
        completed=completed,
        chunk_size=128,
        text_length=6,
    )


async def test_replay_is_new_frames_with_current_context_and_no_provider_call(cache):
    calls = []

    async def source():
        calls.append(True)
        yield TTSAudioRawFrame(PCM, 16000, 1, context_id="old")

    for _ in range(3):
        cold = [f async for f in synthesize(cache, source)]
    warm = [f async for f in synthesize(cache, source, context_id="new")]
    assert len(calls) == 3
    assert b"".join(f.audio for f in warm) == PCM
    assert all(f.context_id == "new" and f.id != cold[0].id for f in warm)
    assert cache.capture_bytes == 0


@pytest.mark.parametrize(
    "service",
    [WordTimestampService(), TokenStreamingService(), UnreportedService()],
    ids=["word_timestamps", "token_streaming", "unreported"],
)
async def test_helper_refuses_services_whose_output_is_not_audio_only(cache, service):
    """The shared helper enforces the contract instead of trusting each adapter.

    Word timestamps are pushed to the pipeline rather than yielded, so the
    frame inspection during capture cannot detect them.
    """
    calls = []

    async def source():
        calls.append(True)
        yield TTSAudioRawFrame(PCM, 16000, 1, context_id="live")

    for _ in range(2):
        frames = [f async for f in synthesize(cache, source, service=service)]
        assert [f.audio for f in frames] == [PCM]
    assert len(calls) == 2
    assert cache.backend.reads == 0
    assert cache.backend.entries == {}
    assert cache.capture_bytes == 0


@pytest.mark.parametrize(
    "mode", ["error", "incomplete", "cancel", "exception", "oversized", "metadata"]
)
async def test_partial_results_are_not_published_and_capture_is_released(cache, mode):
    closed = []
    if mode == "oversized":
        cache.policy = replace(cache.policy, max_entry_bytes=10)

    async def source():
        try:
            yield TTSAudioRawFrame(PCM, 16000, 1)
            if mode == "error":
                yield ErrorFrame("Late provider failure")
            elif mode == "exception":
                raise RuntimeError("Failed response")
            elif mode == "metadata":
                yield Frame()
        finally:
            closed.append(True)

    stream = synthesize(cache, source, completed=lambda: mode != "incomplete")
    if mode == "cancel":
        await anext(stream)
        await stream.aclose()
    elif mode == "exception":
        with pytest.raises(RuntimeError):
            _ = [f async for f in stream]
    else:
        _ = [f async for f in stream]
    assert not cache.backend.entries
    assert not cache.backend.candidates
    assert cache.capture_bytes == 0
    assert closed == [True]


async def test_worker_capture_budget_does_not_stop_live_audio(cache):
    cache.policy = replace(cache.policy, max_capture_bytes=len(PCM))
    assert cache.reserve(len(PCM))

    async def source():
        yield TTSAudioRawFrame(PCM, 16000, 1)

    frames = [f async for f in synthesize(cache, source)]
    assert frames[0].audio == PCM
    assert not cache.backend.entries
    assert cache.capture_bytes == len(PCM)
    cache.release(len(PCM))


async def test_interrupted_replay_keeps_complete_entry(cache):
    await warm_cache(cache, request_for(), CachedSpeech(PCM, 16000))

    async def source():
        pytest.fail("A cache hit must not invoke the provider")
        yield

    stream = synthesize(cache, source)
    await anext(stream)
    await stream.aclose()
    assert (await cache.get(request_for())).audio == PCM
    assert cache.capture_bytes == 0


async def test_storage_failure_does_not_disrupt_live_audio(cache):
    async def failed_write(*args, **kwargs):
        raise ConnectionError("Redis unavailable")

    async def source():
        yield TTSAudioRawFrame(PCM, 16000, 1)

    cache.backend.put = failed_write
    frames = [f async for f in synthesize(cache, source)]
    assert b"".join(f.audio for f in frames) == PCM
    assert not cache.backend.entries
    assert cache.capture_bytes == 0


async def test_timeout_falls_back_and_cancellation_propagates(cache):
    async def stalled(*args):
        await asyncio.sleep(10)

    cache.backend.get = stalled
    async with asyncio.timeout(0.3):
        assert await cache.get(request_for()) is None
    assert await cache.get(request_for()) is None  # Cooldown.
    cache._unavailable_until = 0
    task = asyncio.create_task(cache.get(request_for()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.fixture
async def redis_backend():
    client = Redis.from_url(os.environ["REDIS_URL"], decode_responses=False)
    prefix = f"test:tts:{uuid.uuid4().hex}"
    backend = RedisCacheBackend(client, prefix=prefix)
    await client.ping()
    try:
        yield backend
    finally:
        keys = [key async for key in client.scan_iter(match=f"{prefix}:*")]
        if keys:
            await client.delete(*keys)
        await backend.close()


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
async def test_fourth_request_replays_median_full_duration_across_workers(
    redis_backend, order
):
    # The padded take has the shortest active audio but the longest full audio.
    # Removing its silence would select a different winner.
    takes = [PCM * 2, b"\0" * len(PCM) * 2 + PCM + b"\0" * len(PCM) * 2, PCM * 4]
    calls = []
    policy = CachePolicy(operation_timeout_seconds=1)

    async def source():
        take = takes[order[len(calls)]]
        calls.append(True)
        yield TTSAudioRawFrame(take, 16000, 1)

    for number in range(4):
        # Each request has independent worker state and shares only Redis.
        cache = SpeechCache(redis_backend, policy)
        frames = [f async for f in synthesize(cache, source, context_id=str(number))]
        expected = takes[order[number]] if number < 3 else takes[2]
        assert b"".join(f.audio for f in frames) == expected
        if number == 3:
            assert all(f.context_id == "3" for f in frames)
        assert cache.capture_bytes == 0
        if number < 2:
            assert await redis_backend.list_entries(1, 10) == []
            assert await cache.get(request_for()) is None

    assert len(calls) == 3
    (entry,) = await redis_backend.list_entries(1, 10)
    assert entry["duration_seconds"] == pytest.approx(len(takes[2]) / 32000)
    assert entry["candidate_duration_seconds"] == pytest.approx(
        [len(takes[i]) / 32000 for i in order]
    )
    assert entry["selected_candidate"] == order.index(2) + 1
    assert entry["selection_policy"] == "reserved_median_duration_v1"
    assert entry["hit_count"] == 1
    assert not await redis_backend.client.exists(
        f"{redis_backend._key(request_for())}:candidates"
    )


async def test_concurrent_candidates_promote_once_and_late_writers_do_not_replace(
    redis_backend,
):
    req = request_for()
    policy = CachePolicy()
    values = [CachedSpeech(PCM * n, 16000).encode() for n in (8, 2, 5, 1, 9, 3)]
    results = await asyncio.gather(
        *(submit_to_backend(redis_backend, req, value, policy) for value in values)
    )
    accepted = {count: value for value, (count, _) in zip(values, results) if count}
    assert sorted(count for count, _ in results) == [0, 0, 0, 1, 2, 3]
    expected = sorted(accepted.values(), key=len)[1]
    key = redis_backend._key(req)
    assert await redis_backend.client.get(key) == expected
    assert await redis_backend.client.zcard(redis_backend._index(1)) == 1
    assert not await redis_backend.client.exists(f"{key}:candidates")
    expiry = await redis_backend.client.pexpiretime(key)
    before = await redis_backend.client.hgetall(f"{key}:meta")
    assert await submit_to_backend(redis_backend, req, values[-1], policy) == (0, 0)
    assert await redis_backend.client.pexpiretime(key) == expiry
    assert await redis_backend.client.hgetall(f"{key}:meta") == before


@pytest.mark.parametrize("identical", [False, True])
async def test_equal_durations_and_identical_independent_generations_count(
    redis_backend, identical
):
    req = request_for()
    values = [
        CachedSpeech(
            (b"\x01\x00" if identical else bytes([n, 0])) * 256, 16000
        ).encode()
        for n in (1, 2, 3)
    ]
    for count, value in enumerate(values, 1):
        assert await submit_to_backend(redis_backend, req, value, CachePolicy()) == (
            count,
            0,
        )
    # Arrival order breaks ties deterministically; no PCM-based deduplication.
    assert await redis_backend.client.get(redis_backend._key(req)) == values[1]


@pytest.mark.parametrize("mode", ["error", "incomplete", "cancel", "exception"])
async def test_failed_synthesis_does_not_advance_existing_pool(redis_backend, mode):
    cache = SpeechCache(redis_backend, CachePolicy(operation_timeout_seconds=1))
    req = request_for()
    await collect_candidate(cache, req, CachedSpeech(PCM, 16000))
    pool = f"{redis_backend._key(req)}:candidates"
    before = await redis_backend.client.lrange(pool, 0, -1)

    async def source():
        yield TTSAudioRawFrame(PCM, 16000, 1)
        if mode == "error":
            yield ErrorFrame("late failure")
        elif mode == "exception":
            raise RuntimeError("provider failure")

    stream = synthesize(cache, source, completed=lambda: mode != "incomplete")
    if mode == "cancel":
        await anext(stream)
        await stream.aclose()
    elif mode == "exception":
        with pytest.raises(RuntimeError):
            _ = [f async for f in stream]
    else:
        _ = [f async for f in stream]
    assert await redis_backend.client.lrange(pool, 0, -1) == before
    assert await cache.get(req) is None
    assert cache.capture_bytes == 0


async def test_warming_pool_expires_without_reads_or_previews_renewing_it(
    redis_backend,
):
    req = request_for()
    cache = SpeechCache(redis_backend, CachePolicy(operation_timeout_seconds=1))
    await collect_candidate(cache, req, CachedSpeech(PCM, 16000))
    pool = f"{redis_backend._key(req)}:candidates"
    expiry = await redis_backend.client.pexpiretime(pool)
    assert await cache.get(req) is None
    assert await redis_backend.list_entries(1, 10) == []
    assert await redis_backend.preview(1, req.digest, 1049604) is None
    assert await redis_backend.client.pexpiretime(pool) == expiry
    assert await redis_backend.client.zcard(redis_backend._index(1)) == 1
    # Simulate Redis expiry; the next generation starts a fresh pool.
    await redis_backend.client.pexpire(pool, 0)
    await collect_candidate(cache, req, CachedSpeech(PCM * 2, 16000))
    assert await redis_backend.client.llen(pool) == 3
    assert await cache.get(req) is None


async def test_warming_pool_delete_clear_and_capacity_include_candidates(redis_backend):
    policy = CachePolicy(max_entries_per_org=1, operation_timeout_seconds=1)
    cache = SpeechCache(redis_backend, policy)
    a, b = request_for(), replace(request_for(), digest="b" * 64)
    other = replace(a, organization_id=2)
    for req in (a, other, b):
        await collect_candidate(cache, req, CachedSpeech(PCM, 16000))
    assert not await redis_backend.client.exists(f"{redis_backend._key(a)}:candidates")
    assert await redis_backend.client.zcard(redis_backend._index(1)) == 1
    # Finishing the pool at full capacity must not evict its earlier candidates.
    for _ in range(2):
        await collect_candidate(cache, b, CachedSpeech(PCM, 16000))
    assert await cache.get(b) is not None
    assert await redis_backend.delete_entry(2, a.digest)
    assert not await redis_backend.client.exists(
        f"{redis_backend._key(other)}:candidates"
    )
    await collect_candidate(cache, other, CachedSpeech(PCM, 16000))
    assert await cache.invalidate_organization(1) == 1
    assert await redis_backend.client.exists(f"{redis_backend._key(other)}:candidates")
    assert await cache.invalidate_organization(2) == 1
    assert not await redis_backend.client.exists(
        f"{redis_backend._key(other)}:candidates"
    )


async def test_redis_cap_holds_under_concurrent_admission(redis_backend):
    """Warming pools share the organization capacity with selected entries."""
    policy = CachePolicy(
        ttl_seconds=60, max_entries_per_org=2, operation_timeout_seconds=1
    )
    requests = [replace(request_for(), digest=f"{i:064x}") for i in range(10)]
    results = await asyncio.gather(
        *(
            submit_to_backend(
                redis_backend, r, CachedSpeech(PCM, 16000).encode(), policy
            )
            for r in requests
        )
    )
    assert sum(evicted for _, evicted in results) == 8
    index = redis_backend._index(1)
    assert await redis_backend.client.zcard(index) == 2
    live = 0
    for r in requests:
        live += await redis_backend.client.exists(f"{redis_backend._key(r)}:candidates")
    assert live == 2


async def test_redis_evicts_least_recently_used_and_a_read_protects_an_entry(
    redis_backend,
):
    policy = CachePolicy(
        ttl_seconds=60, max_entries_per_org=3, operation_timeout_seconds=1
    )
    cache = SpeechCache(redis_backend, policy)
    a, b, c, d = (replace(request_for(), digest=f"{i:064x}") for i in range(4))
    for r in (a, b, c):
        await warm_cache(cache, r, CachedSpeech(PCM, 16000))
        await asyncio.sleep(0.01)  # distinct access scores
    # Reading `a` makes `b` the least recently used entry.
    assert await cache.get(a) is not None
    await asyncio.sleep(0.01)
    await warm_cache(cache, d, CachedSpeech(PCM, 16000))
    assert await cache.get(b) is None
    for survivor in (a, c, d):
        assert await cache.get(survivor) is not None


async def test_redis_read_extends_entry_lifetime(redis_backend):
    policy = CachePolicy(
        ttl_seconds=2, max_entries_per_org=4, operation_timeout_seconds=1
    )
    cache = SpeechCache(redis_backend, policy)
    req = request_for()
    await warm_cache(cache, req, CachedSpeech(PCM, 16000))
    for _ in range(3):
        await asyncio.sleep(1.0)
        assert await cache.get(req) is not None  # each read pushes expiry out
    await asyncio.sleep(2.5)
    assert await cache.get(req) is None


async def test_redis_prunes_expired_members_without_counting_them_as_evictions(
    redis_backend,
):
    policy = CachePolicy(
        ttl_seconds=1, max_entries_per_org=2, operation_timeout_seconds=1
    )
    cache = SpeechCache(redis_backend, policy)
    a, b, c = (replace(request_for(), digest=f"{i:064x}") for i in range(3))
    await collect_candidate(cache, a, CachedSpeech(PCM, 16000))
    await collect_candidate(cache, b, CachedSpeech(PCM, 16000))
    await asyncio.sleep(1.2)
    stored, evicted = await submit_to_backend(
        redis_backend, c, CachedSpeech(PCM, 16000).encode(), policy
    )
    assert stored and evicted == 0
    assert await redis_backend.client.zcard(redis_backend._index(1)) == 1


async def test_redis_org_invalidation_and_no_overwrite(redis_backend):
    policy = CachePolicy(
        ttl_seconds=60, max_entries_per_org=8, operation_timeout_seconds=1
    )
    cache = SpeechCache(redis_backend, policy)
    first = request_for()
    await warm_cache(cache, first, CachedSpeech(PCM, 16000))
    assert await submit_to_backend(redis_backend, first, b"replacement", policy) == (
        0,
        0,
    )
    assert await cache.get(first) == CachedSpeech(PCM, 16000)
    await warm_cache(cache, replace(first, organization_id=2), CachedSpeech(PCM, 16000))
    assert await cache.invalidate_organization(1) == 1
    assert await cache.get(first) is None
    assert await cache.get(replace(first, organization_id=2)) is not None


async def test_redis_rejects_oversized_read(redis_backend):
    req = request_for()
    await redis_backend.client.set(redis_backend._key(req), b"x" * 200)
    assert await redis_backend.get(req, 100, 60) is None
    assert not await redis_backend.client.exists(redis_backend._key(req))


@pytest.mark.parametrize(
    "broken", [b"", b"bad", b"xxxx", CachedSpeech(PCM, 24000).encode()]
)
async def test_redis_corrupt_entry_is_replaced_after_successful_synthesis(
    redis_backend, broken
):
    req = request_for()
    policy = CachePolicy(operation_timeout_seconds=1)
    cache = SpeechCache(redis_backend, policy)
    await warm_cache(cache, req, CachedSpeech(PCM, 16000))
    key = redis_backend._key(req)
    await redis_backend.client.set(key, broken, ex=60)
    assert await cache.get(req) is None
    assert not await redis_backend.client.exists(key, f"{key}:meta")
    assert await redis_backend.client.zcard(redis_backend._index(1)) == 0

    async def source():
        yield TTSAudioRawFrame(PCM, 16000, 1)

    for _ in range(3):
        assert [frame async for frame in synthesize(cache, source)]
    assert await cache.get(req) == CachedSpeech(PCM, 16000)


async def test_corruption_cleanup_does_not_delete_a_concurrent_replacement(
    redis_backend,
):
    req = request_for()
    key = redis_backend._key(req)
    await redis_backend.client.set(key, b"bad", ex=60)
    invalid = await redis_backend.get(req, 1024, 60)
    replacement = CachedSpeech(PCM, 16000).encode()
    await redis_backend.client.set(key, replacement, ex=60)
    assert not await redis_backend.delete_if_value(req, invalid)
    assert await redis_backend.client.get(key) == replacement


async def test_redis_metadata_follows_eviction_and_invalidation(redis_backend):
    policy = CachePolicy(max_entries_per_org=1)
    first = request_for()
    second = replace(first, digest="a" * 64)
    cache = SpeechCache(redis_backend, policy)
    await warm_cache(cache, first, CachedSpeech(PCM, 16000))
    await warm_cache(cache, second, CachedSpeech(PCM, 16000))
    assert not await redis_backend.client.exists(f"{redis_backend._key(first)}:meta")
    assert await redis_backend.invalidate_organization(1) == 1
    assert not await redis_backend.client.exists(f"{redis_backend._key(second)}:meta")


async def test_cache_is_disabled_without_workflow_opt_in_or_valid_tenant():
    with patch("api.services.pipecat.tts_cache.runtime.Redis.from_url") as connect:
        for org in (1, 2, 3):
            assert get_speech_cache(org) is None
        for org in (None, 0, -1, True, "1"):
            assert get_speech_cache(org, enabled=True) is None
        connect.assert_not_called()


async def test_cache_uses_existing_redis_and_shared_worker_pool():
    from api.constants import REDIS_URL

    await close_speech_cache()
    with patch(
        "api.services.pipecat.tts_cache.runtime.Redis.from_url", wraps=Redis.from_url
    ) as connect:
        try:
            first = get_speech_cache(1, enabled=True)
            assert get_speech_cache(2, enabled=True) is first
            assert get_speech_cache(1, enabled=False) is None
            connect.assert_called_once()
            assert connect.call_args.args == (REDIS_URL,)
            assert connect.call_args.kwargs["decode_responses"] is False
        finally:
            await close_speech_cache()


@pytest.fixture
async def minimax_server(aiohttp_client):
    requests = []

    async def handler(request):
        payload = await request.json()
        requests.append(payload)
        body = [
            {"data": {"audio": PCM.hex(), "status": 1}},
            {"data": {"audio": "", "status": 2}, "extra_info": {}},
        ]
        if payload["text"].startswith("Fail"):
            body[-1]["base_resp"] = {"status_code": 1002, "status_msg": "rate limit"}
        import json

        return web.Response(
            body=b"".join(b"data:" + json.dumps(p).encode() + b"\n\n" for p in body),
            content_type="text/event-stream",
        )

    app = web.Application()
    app.router.add_post("/tts", handler)
    client = await aiohttp_client(app)
    return str(client.make_url("/tts")), requests


async def play(
    cache, url, texts, *, organization_id=1, rate=16000, settings=None, **kwargs
):
    session = aiohttp.ClientSession()
    service_class = kwargs.pop("service_class", MiniMaxCachingTTSService)
    service = service_class(
        speech_cache=cache,
        organization_id=organization_id,
        api_key="key",
        group_id="group",
        base_url=url,
        aiohttp_session=session,
        sample_rate=rate,
        settings=settings,
        **kwargs,
    )
    down, up = await run_test(
        service,
        frames_to_send=[
            TTSSpeakFrame(text=t) if isinstance(t, str) else t for t in texts
        ],
        pipeline_params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        start_timeout=10,
    )
    assert session.closed
    return down, up


async def test_minimax_pipeline_mixed_hits_misses_transcripts_and_usage(
    cache, minimax_server
):
    url, calls = minimax_server
    await play(cache, url, ["Hello."] * 3)
    assert len(calls) == 3
    # A fresh service instance shares audio across calls, with hit/miss/hit ordering.
    frames, up = await play(cache, url, ["Hello.", "Goodbye.", "Hello."])
    assert len(calls) == 4
    assert not [f for f in frames + up if isinstance(f, ErrorFrame)]
    assert (
        b"".join(f.audio for f in frames if isinstance(f, TTSAudioRawFrame)) == PCM * 3
    )
    assert [f.text for f in frames if isinstance(f, TTSTextFrame)] == [
        "Hello.",
        "Goodbye.",
        "Hello.",
    ]
    assert sum(isinstance(f, TTSStartedFrame) for f in frames) == 3
    assert sum(isinstance(f, TTSStoppedFrame) for f in frames) == 3
    usage = [
        d
        for f in frames
        if isinstance(f, MetricsFrame)
        for d in f.data
        if isinstance(d, TTSUsageMetricsData)
    ]
    assert sum(d.value for d in usage) == len("Goodbye.")
    assert cache.capture_bytes == 0


async def test_minimax_late_failure_never_hits_cache(cache, minimax_server):
    url, calls = minimax_server
    for _ in range(2):
        down, up = await play(cache, url, ["Fail."])
        assert any(isinstance(f, ErrorFrame) for f in down + up)
    assert len(calls) == 2
    assert not cache.backend.entries


async def test_minimax_identity_and_ineligible_token_mode(cache, minimax_server):
    url, calls = minimax_server
    await play(cache, url, ["Hello."])
    await play(cache, url, ["Hello."], organization_id=2)
    await play(cache, url, ["Hello."], rate=24000)
    await play(cache, url, ["Hello."], settings=MiniMaxTTSSettings(speed=1.2))
    assert len(calls) == 4
    reads = cache.backend.reads
    await play(cache, url, ["Hello."], text_aggregation_mode=TextAggregationMode.TOKEN)
    assert cache.backend.reads == reads
    assert len(calls) == 5


async def test_real_adapter_declaring_word_timestamps_never_populates_cache(
    cache, minimax_server
):
    """A provider-eligible request is still refused when the service pushes words."""
    url, calls = minimax_server
    for _ in range(2):
        await play(cache, url, ["Hello."], push_text_frames=False)
    assert len(calls) == 2
    assert cache.backend.reads == 0
    assert cache.backend.entries == {}


async def test_two_sentences_in_one_llm_turn_replay_in_order(cache, minimax_server):
    url, calls = minimax_server
    turn = lambda: [
        LLMFullResponseStartFrame(),
        LLMTextFrame("Hello. Goodbye."),
        LLMFullResponseEndFrame(),
    ]
    first, _ = await play(cache, url, turn())
    for _ in range(2):
        await play(cache, url, turn())
    count = len(calls)
    second, _ = await play(cache, url, turn())
    assert len(calls) == count
    assert [f.text for f in first if isinstance(f, TTSTextFrame)] == [
        f.text for f in second if isinstance(f, TTSTextFrame)
    ]
    assert b"".join(
        f.audio for f in first if isinstance(f, TTSAudioRawFrame)
    ) == b"".join(f.audio for f in second if isinstance(f, TTSAudioRawFrame))


@pytest.mark.parametrize(
    "cached_sentences", [(1, 3), (2,)], ids=["hit-miss-hit", "miss-hit-miss"]
)
@pytest.mark.parametrize(
    ("delay_at", "delay_seconds"),
    [("headers", 0.25), ("headers", 3.25), ("second_chunk", 3.25)],
    ids=[
        "slow-response",
        "response-past-context-timeout",
        "stream-past-context-timeout",
    ],
)
async def test_minimax_delayed_http_and_redis_preserve_playback_order(
    redis_backend, aiohttp_client, cached_sentences, delay_at, delay_seconds
):
    """Exercise real Redis, streaming HTTP, and the output transport's audio queue.

    The local HTTP server emits distinct PCM per sentence and delays cache
    misses. Long delays cross the default three-second audio-context timeout;
    a delayed first byte on sentence one also reports a recoverable error.
    """
    sentences = ["First sentence.", "Second sentence.", "Third sentence."]
    # 160 ms per sentence, aligned to the transport's 40 ms audio chunks.
    audio = {number: number.to_bytes(2, "little") * 2560 for number in (1, 2, 3)}
    events = []
    requests = []
    inject_delay = False

    def record(kind, number):
        events.append((kind, number, time.monotonic()))

    def sentence_number(text):
        return sentences.index(text.strip()) + 1

    async def handler(request):
        payload = await request.json()
        number = sentence_number(payload["text"])
        requests.append(number)
        record("http_start", number)
        if inject_delay and delay_at == "headers":
            await asyncio.sleep(delay_seconds)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        midpoint = len(audio[number]) // 2
        for index, chunk in enumerate(
            (audio[number][:midpoint], audio[number][midpoint:])
        ):
            if inject_delay and delay_at == "second_chunk" and index == 1:
                await asyncio.sleep(delay_seconds)
            payload = {"data": {"audio": chunk.hex(), "status": 1}}
            await response.write(b"data:" + json.dumps(payload).encode() + b"\n\n")
            record(f"http_chunk_{index + 1}", number)
        await response.write(b'data:{"data":{"audio":"","status":2}}\n\n')
        await response.write_eof()
        record("http_end", number)
        return response

    app = web.Application()
    app.router.add_post("/tts", handler)
    client = await aiohttp_client(app)
    url = str(client.make_url("/tts"))
    cache = SpeechCache(redis_backend, CachePolicy(operation_timeout_seconds=1))

    def turn():
        return [
            LLMFullResponseStartFrame(),
            LLMTextFrame(" ".join(sentences)),
            LLMFullResponseEndFrame(),
        ]

    # Collect three candidates per sentence via real HTTP using the exact
    # aggregation/payloads to replay, then remove the entries meant to miss.
    for _ in range(3):
        warm, warm_up = await play(cache, url, turn())
        assert not any(isinstance(frame, ErrorFrame) for frame in warm + warm_up)
    assert requests == [1, 2, 3] * 3
    entries = await redis_backend.list_entries(1, 10)
    assert len(entries) == 3
    for entry in entries:
        if sentence_number(entry["text_preview"]) not in cached_sentences:
            assert await redis_backend.delete_entry(1, entry["id"])

    events.clear()
    requests.clear()
    inject_delay = True
    started = time.monotonic()
    original_get = redis_backend.get

    async def traced_get(request, max_bytes, ttl_seconds):
        value = await original_get(request, max_bytes, ttl_seconds)
        record(
            "redis_hit" if value is not None else "redis_miss",
            sentence_number(request.text_preview),
        )
        return value

    class RecordingOutputTransport(MockOutputTransport):
        """Capture PCM where the production MediaSender writes to the device."""

        def __init__(self):
            super().__init__(
                TransportParams(
                    audio_out_enabled=True,
                    audio_out_sample_rate=16000,
                    audio_out_end_silence_secs=0,
                    audio_out_auto_silence=False,
                )
            )
            self.audio = bytearray()

        async def write_audio_frame(self, frame):
            if frame.audio:
                self.audio.extend(frame.audio)
                record("playback", int.from_bytes(frame.audio[:2], "little"))
            return await super().write_audio_frame(frame)

    output = RecordingOutputTransport()
    async with aiohttp.ClientSession() as session:
        service = MiniMaxCachingTTSService(
            speech_cache=cache,
            organization_id=1,
            api_key="key",
            group_id="group",
            base_url=url,
            aiohttp_session=session,
            sample_rate=16000,
        )
        with patch.object(redis_backend, "get", side_effect=traced_get):
            async with asyncio.timeout(20):
                down, up = await run_test(
                    Pipeline([service, output]),
                    frames_to_send=turn(),
                    start_timeout=10,
                )

    errors = [frame for frame in down + up if isinstance(frame, ErrorFrame)]
    if delay_at == "headers" and delay_seconds > 3 and 1 not in cached_sentences:
        # The idle context expires before the first audio arrives. Its error is
        # reported upstream, but late audio recreates the context in this turn.
        assert len(errors) == 1
        assert errors[0].error.endswith("completed with no audio")
        assert not errors[0].fatal
    else:
        assert not errors
    assert bytes(output.audio) == audio[1] + audio[2] + audio[3]
    assert [
        frame.text.strip() for frame in down if isinstance(frame, TTSTextFrame)
    ] == sentences
    assert requests == [
        number for number in (1, 2, 3) if number not in cached_sentences
    ]
    lookups = [
        (kind, number) for kind, number, _ in events if kind.startswith("redis_")
    ]
    assert lookups == [
        ("redis_hit" if number in cached_sentences else "redis_miss", number)
        for number in (1, 2, 3)
    ]

    def when(kind, number):
        return next(
            at for event, sentence, at in events if (event, sentence) == (kind, number)
        )

    # The next sentence's Redis lookup cannot overtake an unfinished HTTP
    # synthesis, even though all three sentences arrived in a single LLM frame.
    for number in requests:
        if number < 3:
            next_lookup = (
                "redis_hit" if number + 1 in cached_sentences else "redis_miss"
            )
            assert when(next_lookup, number + 1) >= when("http_end", number)
        before_delay = "http_start" if delay_at == "headers" else "http_chunk_1"
        after_delay = "http_chunk_1" if delay_at == "headers" else "http_chunk_2"
        assert when(after_delay, number) - when(before_delay, number) >= delay_seconds

    # A miss contributes only one candidate, so it is not yet a selected entry.
    entries = await redis_backend.list_entries(1, 10)
    assert len(entries) == len(cached_sentences)
    assert {
        sentence_number(entry["text_preview"]): entry["hit_count"] for entry in entries
    } == {number: 1 for number in cached_sentences}
    assert cache.capture_bytes == 0
    assert isinstance(down[-1], LLMFullResponseEndFrame)
    print(f"\nCached={cached_sentences}, delay={delay_seconds}s at {delay_at}")
    for number in (1, 2, 3):
        lookup = "redis_hit" if number in cached_sentences else "redis_miss"
        print(
            f"  Sentence {number}: {lookup} at {when(lookup, number) - started:.3f}s; "
            f"first playback at {when('playback', number) - started:.3f}s"
        )


async def test_pipeline_interruption_discards_partial_capture(cache, minimax_server):
    url, calls = minimax_server
    cancelled = []

    class SlowMiniMax(MiniMaxCachingTTSService):
        async def _run_tts_request(self, payload, context_id, outcome):
            if payload["text"] == "Slow.":
                try:
                    yield TTSAudioRawFrame(PCM, 16000, 1, context_id=context_id)
                    await asyncio.sleep(10)
                    outcome.completed = True
                finally:
                    cancelled.append(True)
            else:
                async for frame in super()._run_tts_request(
                    payload, context_id, outcome
                ):
                    yield frame

    async with asyncio.timeout(5):
        down, up = await play(
            cache,
            url,
            [
                "Slow.",
                SleepFrame(sleep=0.1),
                InterruptionFrame(),
                SleepFrame(sleep=0.1),
                "Hello.",
            ],
            service_class=SlowMiniMax,
        )
    assert cancelled == [True]
    assert not any(isinstance(f, ErrorFrame) for f in down + up)
    assert len(calls) == 1
    assert not cache.backend.entries
    assert len(cache.backend.candidates) == 1
    assert cache.capture_bytes == 0
    await play(cache, url, ["Hello."] * 3)
    assert len(calls) == 3
    assert len(cache.backend.entries) == 1


async def test_runtime_settings_change_cache_identity(cache, minimax_server):
    url, calls = minimax_server
    await play(cache, url, ["Hello."] * 3)
    calls.clear()
    down, up = await play(
        cache,
        url,
        [
            "Hello.",
            TTSUpdateSettingsFrame(delta=MiniMaxTTSSettings(speed=1.25)),
            "Hello.",
            TTSUpdateSettingsFrame(delta=MiniMaxTTSSettings(speed=1.0)),
            "Hello.",
        ],
    )
    assert not any(isinstance(f, ErrorFrame) for f in down + up)
    assert [call["voice_setting"]["speed"] for call in calls] == [1.25]
    assert b"".join(f.audio for f in down if isinstance(f, TTSAudioRawFrame)) == PCM * 3
