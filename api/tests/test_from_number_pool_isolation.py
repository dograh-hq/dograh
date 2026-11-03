"""Caller-ID rotation and dial-rate buckets stay isolated across accounts/configs."""

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from api.services.call_concurrency.rate_limiter import RateLimiter


@pytest.fixture
async def limiter():
    rl = RateLimiter()
    redis = await rl._get_redis()
    keys = set()
    original = redis.eval

    async def track(script, count, *args):
        keys.update(args[:count])
        return await original(script, count, *args)

    redis.eval = track
    yield rl
    redis.eval = original
    if keys:
        await redis.delete(*keys)
    await rl.close()


@pytest.mark.asyncio
async def test_rotation_reuses_numbers_without_reservations_and_isolates_configs(
    limiter,
):
    org = uuid.uuid4().int % 10**9
    numbers = ["cli-b", "cli-a", "cli-a"]
    assert [await limiter.select_from_number(org, 1, numbers) for _ in range(5)] == [
        "cli-a",
        "cli-b",
        "cli-a",
        "cli-b",
        "cli-a",
    ]
    assert await limiter.select_from_number(org, 2, ["other-cli"]) == "other-cli"
    assert await limiter.select_from_number(org + 1, 1, numbers) == "cli-a"
    # Removed addresses must not survive in a Redis pool; selection uses this snapshot.
    assert await limiter.select_from_number(org, 1, ["new-cli"]) == "new-cli"


@pytest.mark.asyncio
async def test_parallel_rotation_balances_without_exhausting(limiter):
    org = uuid.uuid4().int % 10**9
    selected = await asyncio.gather(
        *[limiter.select_from_number(org, 1, ["a", "b"]) for _ in range(200)]
    )
    assert selected.count("a") == selected.count("b") == 100


@pytest.mark.asyncio
async def test_rotation_failure_falls_back_and_empty_list_remains_optional():
    rl = RateLimiter()
    with patch.object(
        rl, "_get_redis", AsyncMock(side_effect=ConnectionError("offline"))
    ):
        assert await rl.select_from_number(1, 1, ["b", "a"]) == "a"
        assert await rl.select_from_number(1, 1, []) is None


@pytest.mark.asyncio
async def test_campaign_buckets_and_waits_are_independent(limiter):
    org = uuid.uuid4().int % 10**9
    scope_a, scope_b = f"test:campaign:{org}:a", f"test:campaign:{org}:b"
    assert await limiter.acquire_token(org, 1, scope_key=scope_a)
    assert not await limiter.acquire_token(org, 1, scope_key=scope_a)
    assert 0 < await limiter.get_next_available_slot(org, 1, scope_key=scope_a) <= 1
    assert await limiter.get_next_available_slot(org, 4, scope_key=scope_b) == 0
    assert all(
        [await limiter.acquire_token(org, 4, scope_key=scope_b) for _ in range(4)]
    )
    assert not await limiter.acquire_token(org, 4, scope_key=scope_b)
    await asyncio.sleep(1.01)
    assert await limiter.acquire_token(org, 1, scope_key=scope_a)


@pytest.mark.asyncio
async def test_concurrent_tokens_with_identical_timestamps_are_counted_separately(
    limiter,
):
    org = uuid.uuid4().int % 10**9
    with patch(
        "api.services.call_concurrency.rate_limiter.time.time", return_value=1234567890
    ):
        admitted = await asyncio.gather(
            *[
                limiter.acquire_token(org, 4, scope_key=f"test:campaign:{org}")
                for _ in range(20)
            ]
        )
    assert sum(admitted) == 4


@pytest.mark.asyncio
async def test_two_caller_ids_can_fill_200_real_slots_without_exceeding_org_limit():
    rl = RateLimiter()
    org = uuid.uuid4().int % 10**9
    scope = f"test:campaign:{org}"
    slots = []
    numbers = []
    try:
        for _ in range(200):
            slot = await rl.try_acquire_concurrent_slot_details(
                org,
                200,
                scope_key=scope,
                scope_max_concurrent=200,
            )
            assert slot is not None
            slots.append(slot)
            numbers.append(await rl.select_from_number(org, 55, ["a", "b"]))
        assert len(set(numbers)) == 2
        assert await rl.get_concurrent_count(org) == 200
        assert await rl.try_acquire_concurrent_slot_details(org, 200) is None
    finally:
        # Only release this test's fleet members; never delete the shared fleet set.
        for slot in slots:
            await rl.release_concurrent_slot(org, slot.slot_id, scope_key=scope)
        redis = await rl._get_redis()
        await redis.delete(
            f"concurrent_calls:{org}",
            f"concurrent_calls:{scope}",
            f"caller_id_rotation:{org}:55",
        )
        await rl.close()
