"""Contributor selection and lease ownership against isolated real Redis keys."""

import asyncio
from dataclasses import replace

import pytest
from pipecat.frames.frames import TTSAudioRawFrame

from api.services.pipecat.tts_cache.cache import SpeechCache
from api.services.pipecat.tts_cache.models import CachedSpeech, CachePolicy
from api.tests import test_tts_cache
from api.tests.test_tts_cache import (
    PCM,
    collect_candidate,
    request_for,
    submit_to_backend,
    synthesize,
    warm_cache,
)

pytestmark = pytest.mark.asyncio
redis_backend = test_tts_cache.redis_backend


async def test_unreserved_requests_finishing_first_cannot_enter_pool(redis_backend):
    req = request_for()
    takes = [PCM * n for n in (11, 10, 9, 8, 7, 6)]
    started = [asyncio.Event() for _ in takes]
    finish = [asyncio.Event() for _ in takes]
    policy = CachePolicy(operation_timeout_seconds=1)
    workers = [SpeechCache(redis_backend, policy) for _ in takes]

    async def run(index):
        async def source():
            started[index].set()
            await finish[index].wait()
            yield TTSAudioRawFrame(takes[index], 16000, 1)

        return [f async for f in synthesize(workers[index], source)]

    tasks = []
    try:
        async with asyncio.timeout(5):
            for index in range(6):
                tasks.append(asyncio.create_task(run(index)))
                await started[index].wait()
            key = redis_backend._key(req)
            assert await redis_backend.client.zcard(f"{key}:reservations") == 3
            # All six streams are already running; the shortest three finish first.
            for index in (5, 4, 3):
                finish[index].set()
                assert b"".join(f.audio for f in await tasks[index]) == takes[index]
                assert await redis_backend.client.llen(f"{key}:candidates") == 0
            for index in (2, 1, 0):
                finish[index].set()
                assert b"".join(f.audio for f in await tasks[index]) == takes[index]

        selected = await workers[0].get(req)
        assert selected.audio == takes[1]  # 10, not the early-finish median of 7.
        (entry,) = await redis_backend.list_entries(1, 10)
        assert entry["candidate_duration_seconds"] == pytest.approx(
            [len(takes[i]) / 32000 for i in (2, 1, 0)]
        )
        assert not await redis_backend.client.exists(
            f"{key}:reservations", f"{key}:candidates"
        )
        assert all(worker.capture_bytes == 0 for worker in workers)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_completed_candidates_and_active_leases_share_three_slots(redis_backend):
    req, policy = request_for(), CachePolicy()
    value = CachedSpeech(PCM, 16000).encode()
    assert await submit_to_backend(redis_backend, req, value, policy) == (1, 0)
    assert (await redis_backend.claim_candidate(req, "second", policy))[0]
    assert (await redis_backend.claim_candidate(req, "third", policy))[0]
    assert not (await redis_backend.claim_candidate(req, "fourth", policy))[0]
    assert await redis_backend.put(req, value, policy, token="fourth") == (0, 0)
    assert await redis_backend.put(req, value, policy, token="second") == (2, 0)
    # Retrying the same completion cannot count as another independent take.
    assert await redis_backend.put(req, value, policy, token="second") == (0, 0)
    assert not (await redis_backend.claim_candidate(req, "fifth", policy))[0]
    assert await redis_backend.put(req, value, policy, token="third") == (3, 0)


async def test_cancelled_stream_releases_slot_for_next_request(redis_backend):
    cache = SpeechCache(redis_backend, CachePolicy(operation_timeout_seconds=1))
    req = request_for()
    started = asyncio.Event()

    async def source():
        started.set()
        yield TTSAudioRawFrame(PCM, 16000, 1)
        await asyncio.Event().wait()

    async def run():
        return [f async for f in synthesize(cache, source)]

    async with asyncio.timeout(5):
        task = asyncio.create_task(run())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert cache.capture_bytes == 0
    assert not await redis_backend.client.exists(*redis_backend._keys(1, req.digest))
    await warm_cache(cache, req, CachedSpeech(PCM, 16000))
    assert await cache.get(req) == CachedSpeech(PCM, 16000)


async def test_expired_reservation_is_replaced_and_old_owner_cannot_write(
    redis_backend,
):
    req = request_for()
    policy = CachePolicy(reservation_ttl_seconds=0.02)
    value = CachedSpeech(PCM, 16000).encode()
    assert (await redis_backend.claim_candidate(req, "expired", policy))[0]
    await asyncio.sleep(0.04)
    assert (await redis_backend.claim_candidate(req, "replacement", CachePolicy()))[0]
    assert await redis_backend.put(req, value, policy, token="expired") == (0, 0)
    await redis_backend.release_candidate(req, "expired")
    leases = f"{redis_backend._key(req)}:reservations"
    assert await redis_backend.client.zcard(leases) == 1
    assert await redis_backend.put(req, value, CachePolicy(), token="replacement") == (
        1,
        0,
    )


@pytest.mark.parametrize("removal", ["delete", "clear", "evict"])
async def test_invalidated_inflight_writer_cannot_repopulate_pool(
    redis_backend, removal
):
    req = request_for()
    policy = CachePolicy(max_entries_per_org=1)
    assert (await redis_backend.claim_candidate(req, "stale", policy))[0]
    # Management reads must preserve a reservation-only pool in the org index.
    assert await redis_backend.list_entries(1, 10) == []
    assert await redis_backend.get(req, 1049604, 86400) is None
    if removal == "delete":
        assert await redis_backend.delete_entry(1, req.digest)
    elif removal == "clear":
        assert await redis_backend.invalidate_organization(1) == 1
    else:
        other = replace(req, digest="b" * 64)
        assert await redis_backend.claim_candidate(other, "other", policy) == (True, 1)
    assert (await redis_backend.claim_candidate(req, "fresh", policy))[0]
    value = CachedSpeech(PCM, 16000).encode()
    assert await redis_backend.put(req, value, policy, token="stale") == (0, 0)
    await redis_backend.release_candidate(req, "stale")
    assert await redis_backend.put(req, value, policy, token="fresh") == (1, 0)


async def test_reservation_tokens_are_scoped_to_request_and_organization(redis_backend):
    req, policy = request_for(), CachePolicy()
    value = CachedSpeech(PCM, 16000).encode()
    assert (await redis_backend.claim_candidate(req, "owner", policy))[0]
    for other in (replace(req, organization_id=2), replace(req, digest="b" * 64)):
        assert await redis_backend.put(other, value, policy, token="owner") == (0, 0)
        await redis_backend.release_candidate(other, "owner")
    assert await redis_backend.put(req, value, policy, token="owner") == (1, 0)


@pytest.mark.parametrize("damage", ["metadata", "duration", "nan", "shape", "type"])
async def test_corrupt_pool_recovers_without_unbounded_growth_or_call_failure(
    redis_backend, damage
):
    req = request_for()
    cache = SpeechCache(redis_backend, CachePolicy(operation_timeout_seconds=1))
    for n in (1, 2):
        await collect_candidate(cache, req, CachedSpeech(PCM * n, 16000))
    token = await cache.claim_candidate(req)
    pool = f"{redis_backend._key(req)}:candidates"
    if damage == "metadata":
        await redis_backend.client.lset(pool, 5, b"broken JSON")
    elif damage in ("duration", "nan"):
        await redis_backend.client.lset(
            pool, 0, b"NaN" if damage == "nan" else b"invalid"
        )
    elif damage == "shape":
        await redis_backend.client.rpush(pool, b"unexpected")
    else:
        await redis_backend.client.set(pool, b"wrong type")
    await cache.put(req, CachedSpeech(PCM * 3, 16000), token=token)
    assert not await redis_backend.client.exists(pool)
    assert await cache.get(req) is None
    await warm_cache(cache, req, CachedSpeech(PCM, 16000))
    assert await cache.get(req) == CachedSpeech(PCM, 16000)


async def test_claim_repairs_corrupt_pool_before_choosing_contributors(redis_backend):
    req, policy = request_for(), CachePolicy()
    pool = f"{redis_backend._key(req)}:candidates"
    await redis_backend.client.rpush(pool, b"invalid list")
    assert (await redis_backend.claim_candidate(req, "fresh", policy))[0]
    assert await redis_backend.put(
        req, CachedSpeech(PCM, 16000).encode(), policy, token="fresh"
    ) == (1, 0)


async def test_existing_pool_enforces_reduced_capacity_without_evicting_itself(
    redis_backend,
):
    requests = [replace(request_for(), digest=f"{i:064x}") for i in range(3)]
    value = CachedSpeech(PCM, 16000).encode()
    for req in requests:
        await submit_to_backend(
            redis_backend, req, value, CachePolicy(max_entries_per_org=3)
        )
    policy = CachePolicy(max_entries_per_org=1)
    assert await redis_backend.claim_candidate(requests[0], "second", policy) == (
        True,
        2,
    )
    assert await redis_backend.client.zcard(redis_backend._index(1)) == 1
    assert await redis_backend.put(requests[0], value, policy, token="second") == (2, 0)
    assert await submit_to_backend(redis_backend, requests[0], value, policy) == (3, 0)


async def test_short_cache_ttl_does_not_orphan_longer_lived_reservations(redis_backend):
    req = request_for()
    other = replace(req, digest="b" * 64)
    policy = CachePolicy(ttl_seconds=1, reservation_ttl_seconds=10)
    assert (await redis_backend.claim_candidate(req, "slow", policy))[0]
    await asyncio.sleep(1.1)
    # A new claim prunes old scores; the live lease must survive that pruning.
    value = CachedSpeech(PCM, 16000).encode()
    for _ in range(3):
        await submit_to_backend(redis_backend, other, value, policy)
    assert await redis_backend.get(other, 1049604, 1) is not None
    assert await redis_backend.client.zcard(redis_backend._index(1)) == 2
    assert await redis_backend.client.ttl(redis_backend._index(1)) >= 8
    assert await redis_backend.invalidate_organization(1) == 2
    assert await redis_backend.put(req, value, policy, token="slow") == (0, 0)


@pytest.mark.parametrize("reserve_all_first", [True, False])
async def test_completed_candidates_survive_until_other_reservations_finish(
    redis_backend, reserve_all_first
):
    req = request_for()
    policy = CachePolicy(ttl_seconds=1, reservation_ttl_seconds=10)
    values = [CachedSpeech(PCM * n, 16000).encode() for n in (2, 1, 3)]
    key = redis_backend._key(req)
    pool, leases = f"{key}:candidates", f"{key}:reservations"

    async with asyncio.timeout(8):
        assert (await redis_backend.claim_candidate(req, "first", policy))[0]
        if reserve_all_first:
            for token in ("second", "third"):
                assert (await redis_backend.claim_candidate(req, token, policy))[0]
        assert await redis_backend.put(req, values[0], policy, token="first") == (1, 0)
        if not reserve_all_first:
            # Later claims must extend an already completed candidate's lifetime.
            for token in ("second", "third"):
                assert (await redis_backend.claim_candidate(req, token, policy))[0]

        deadline = await redis_backend.client.zscore(leases, "third")
        await asyncio.sleep(1.1)
        assert await redis_backend.client.lindex(pool, 1) == values[0]
        assert await redis_backend.put(req, values[1], policy, token="second") == (2, 0)
        # Refreshing the pool on another submission must preserve both recordings.
        await asyncio.sleep(1.1)
        assert await redis_backend.client.llen(pool) == 6
        assert await redis_backend.client.zscore(leases, "third") == deadline
        assert await redis_backend.put(req, values[2], policy, token="third") == (3, 0)

    # The first completed recording is the median and must still be available.
    assert await redis_backend.client.get(key) == values[0]
    (entry,) = await redis_backend.list_entries(1, 10)
    assert entry["candidate_duration_seconds"] == pytest.approx(
        [len(PCM) * n / 32000 for n in (2, 1, 3)]
    )
    assert entry["selected_candidate"] == 1
    assert not await redis_backend.client.exists(pool, leases)
    assert 0 < await redis_backend.client.pttl(key) <= 1000
    assert 0 < await redis_backend.client.pttl(f"{key}:meta") <= 1000


async def test_claim_failure_falls_back_to_live_audio(redis_backend, monkeypatch):
    cache = SpeechCache(redis_backend, CachePolicy(operation_timeout_seconds=0.02))

    async def stalled(*args):
        await asyncio.Event().wait()

    async def source():
        yield TTSAudioRawFrame(PCM, 16000, 1)

    monkeypatch.setattr(redis_backend, "claim_candidate", stalled)
    async with asyncio.timeout(0.5):
        frames = [f async for f in synthesize(cache, source)]
    assert b"".join(f.audio for f in frames) == PCM
    assert cache.capture_bytes == 0
    assert await redis_backend.client.zcard(redis_backend._index(1)) == 0
