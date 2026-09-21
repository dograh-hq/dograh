"""Cache isolation, failure behavior, and MiniMax replay through real pipelines."""

import asyncio
import copy
import os
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
from pipecat.pipeline.worker import PipelineParams
from pipecat.services.minimax.tts import MiniMaxTTSSettings
from pipecat.services.tts_service import TextAggregationMode
from pipecat.tests.utils import SleepFrame, run_test
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
    """Test double with the production LRU admission behavior.

    ``entries`` is ordered by last access so the first key is the LRU victim,
    mirroring the index sorted set the Redis backend keeps.
    """

    def __init__(self):
        self.entries = {}
        self.reads = 0
        self.evicted = []

    async def get(self, request, max_bytes, ttl_seconds):
        self.reads += 1
        key = (request.organization_id, request.digest)
        value = self.entries.get(key)
        if value is not None:
            self.entries[key] = self.entries.pop(key)  # refresh recency
        return value

    async def put(self, request, value, policy):
        key = (request.organization_id, request.digest)
        if key in self.entries:
            self.entries[key] = self.entries.pop(key)
            return False, 0
        evicted = 0
        while len(self.entries) >= policy.max_entries_per_org:
            self.evicted.append(next(iter(self.entries)))
            del self.entries[self.evicted[-1]]
            evicted += 1
        self.entries[key] = value
        return True, evicted

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
    await cache.put(request_for(), CachedSpeech(PCM, 16000))
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
    await cache.put(request_for(payload=payload), CachedSpeech(PCM, 16000))
    changed = copy.deepcopy(payload)
    changed[field] = value
    assert await cache.get(request_for(payload=changed)) is None


async def test_corruption_and_format_mismatch_are_misses(cache):
    req = request_for()
    await cache.put(req, CachedSpeech(PCM, 16000))
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

    cold = [f async for f in synthesize(cache, source)]
    warm = [f async for f in synthesize(cache, source, context_id="new")]
    assert len(calls) == 1
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
    await cache.put(request_for(), CachedSpeech(PCM, 16000))

    async def source():
        pytest.fail("A cache hit must not invoke the provider")
        yield

    stream = synthesize(cache, source)
    await anext(stream)
    await stream.aclose()
    assert (await cache.get(request_for())).audio == PCM
    assert cache.capture_bytes == 0


async def test_storage_failure_does_not_disrupt_live_audio(cache):
    async def failed_write(*args):
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


async def test_redis_cap_holds_under_concurrent_admission(redis_backend):
    """Every writer is admitted now, so the cap is the invariant that must hold."""
    policy = CachePolicy(
        ttl_seconds=60, max_entries_per_org=2, operation_timeout_seconds=1
    )
    requests = [replace(request_for(), digest=f"{i:064x}") for i in range(10)]
    results = await asyncio.gather(
        *(
            redis_backend.put(r, CachedSpeech(PCM, 16000).encode(), policy)
            for r in requests
        )
    )
    assert sum(stored for stored, _ in results) == 10
    index = redis_backend._index(1)
    assert await redis_backend.client.zcard(index) == 2
    live = 0
    for r in requests:
        live += await redis_backend.client.exists(redis_backend._key(r))
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
        await cache.put(r, CachedSpeech(PCM, 16000))
        await asyncio.sleep(0.01)  # distinct access scores
    # Reading `a` makes `b` the least recently used entry.
    assert await cache.get(a) is not None
    await asyncio.sleep(0.01)
    await cache.put(d, CachedSpeech(PCM, 16000))
    assert await cache.get(b) is None
    for survivor in (a, c, d):
        assert await cache.get(survivor) is not None


async def test_redis_read_extends_entry_lifetime(redis_backend):
    policy = CachePolicy(
        ttl_seconds=2, max_entries_per_org=4, operation_timeout_seconds=1
    )
    cache = SpeechCache(redis_backend, policy)
    req = request_for()
    await cache.put(req, CachedSpeech(PCM, 16000))
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
    await cache.put(a, CachedSpeech(PCM, 16000))
    await cache.put(b, CachedSpeech(PCM, 16000))
    await asyncio.sleep(1.2)
    stored, evicted = await redis_backend.put(
        c, CachedSpeech(PCM, 16000).encode(), policy
    )
    assert stored and evicted == 0
    assert await redis_backend.client.zcard(redis_backend._index(1)) == 1


async def test_redis_org_invalidation_and_no_overwrite(redis_backend):
    policy = CachePolicy(
        ttl_seconds=60, max_entries_per_org=8, operation_timeout_seconds=1
    )
    cache = SpeechCache(redis_backend, policy)
    first = request_for()
    await cache.put(first, CachedSpeech(PCM, 16000))
    assert await redis_backend.put(first, b"replacement", policy) == (False, 0)
    assert await cache.get(first) == CachedSpeech(PCM, 16000)
    await cache.put(replace(first, organization_id=2), CachedSpeech(PCM, 16000))
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
    await cache.put(req, CachedSpeech(PCM, 16000))
    key = redis_backend._key(req)
    await redis_backend.client.set(key, broken, ex=60)
    assert await cache.get(req) is None
    assert not await redis_backend.client.exists(key, f"{key}:meta")
    assert await redis_backend.client.zcard(redis_backend._index(1)) == 0

    async def source():
        yield TTSAudioRawFrame(PCM, 16000, 1)

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
    await redis_backend.put(first, CachedSpeech(PCM, 16000).encode(), policy)
    await redis_backend.put(second, CachedSpeech(PCM, 16000).encode(), policy)
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
    )
    assert session.closed
    return down, up


async def test_minimax_pipeline_mixed_hits_misses_transcripts_and_usage(
    cache, minimax_server
):
    url, calls = minimax_server
    await play(cache, url, ["Hello."])
    assert len(calls) == 1
    # A fresh service instance shares audio across calls, with hit/miss/hit ordering.
    frames, up = await play(cache, url, ["Hello.", "Goodbye.", "Hello."])
    assert len(calls) == 2
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
    count = len(calls)
    second, _ = await play(cache, url, turn())
    assert len(calls) == count
    assert [f.text for f in first if isinstance(f, TTSTextFrame)] == [
        f.text for f in second if isinstance(f, TTSTextFrame)
    ]
    assert b"".join(
        f.audio for f in first if isinstance(f, TTSAudioRawFrame)
    ) == b"".join(f.audio for f in second if isinstance(f, TTSAudioRawFrame))


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
    assert len(cache.backend.entries) == 1
    assert cache.capture_bytes == 0
    await play(cache, url, ["Hello."])
    assert len(calls) == 1


async def test_runtime_settings_change_cache_identity(cache, minimax_server):
    url, calls = minimax_server
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
    assert [call["voice_setting"]["speed"] for call in calls] == [1.0, 1.25]
    assert b"".join(f.audio for f in down if isinstance(f, TTSAudioRawFrame)) == PCM * 3
