"""Unit tests for RateLimiter.get_fleet_concurrent_count — the fleet-wide
autoscaling signal (AUTOSCALING_PLAN.md Phase 1).

Fakes the Redis client (scored sorted sets) so no real Redis is needed. The
behaviors that matter:
  - org counters are summed; campaign scope counters are skipped (a scoped call
    lives in both keys, so counting scope keys would double-count);
  - stale slots (older than stale_call_timeout) are excluded via ZCOUNT-by-score,
    so an orphaned call can't keep the metric high and block scale-down;
  - duplicate keys from a SCAN pass are counted once.
"""

import time
from unittest.mock import AsyncMock

import pytest

from api.services.campaign.rate_limiter import RateLimiter

_NOW = time.time()


class _FakeRedis:
    """Async Redis stub: scan_iter + zcount over in-memory scored sets.

    ``sets`` maps key -> list of member scores. ``dup_keys`` optionally makes
    scan_iter yield a key more than once (SCAN may return duplicates).
    """

    def __init__(self, sets: dict[str, list[float]], *, dup_keys=()):
        self._sets = sets
        self._dup_keys = dup_keys

    async def scan_iter(self, match: str, count: int = 100):
        prefix = match.rstrip("*")
        for key in self._sets:
            if key.startswith(prefix):
                yield key
                if key in self._dup_keys:
                    yield key  # SCAN can return the same key twice

    async def zcount(self, key: str, min_score, max_score) -> int:
        lo = float(min_score)
        return sum(1 for s in self._sets[key] if s >= lo)  # max is "+inf"


def _rl_with(redis) -> RateLimiter:
    rl = RateLimiter()
    rl._get_redis = AsyncMock(return_value=redis)  # type: ignore[method-assign]
    return rl


@pytest.mark.asyncio
async def test_sums_org_keys_and_skips_scope_keys():
    redis = _FakeRedis(
        {
            "concurrent_calls:1": [_NOW, _NOW, _NOW],  # 3
            "concurrent_calls:22": [_NOW] * 5,  # 5
            "concurrent_calls:campaign:9": [_NOW] * 4,  # scope key — excluded
            "concurrent_calls:campaign:staging": [_NOW] * 2,  # non-numeric — excluded
        }
    )
    assert await _rl_with(redis).get_fleet_concurrent_count() == 8


@pytest.mark.asyncio
async def test_excludes_stale_slots():
    rl = _rl_with(
        _FakeRedis(
            {
                # 2 fresh + 3 stale (older than the 1200s stale timeout)
                "concurrent_calls:1": [_NOW, _NOW, _NOW - 5000, _NOW - 5000, _NOW - 5000],
            }
        )
    )
    assert await rl.get_fleet_concurrent_count() == 2


@pytest.mark.asyncio
async def test_duplicate_scan_key_counted_once():
    redis = _FakeRedis(
        {"concurrent_calls:1": [_NOW] * 4}, dup_keys=("concurrent_calls:1",)
    )
    # Without dedup this would report 8; the key must be counted once.
    assert await _rl_with(redis).get_fleet_concurrent_count() == 4


@pytest.mark.asyncio
async def test_empty_fleet_is_zero():
    assert await _rl_with(_FakeRedis({})).get_fleet_concurrent_count() == 0


@pytest.mark.asyncio
async def test_redis_error_returns_zero():
    class _Boom:
        async def scan_iter(self, match, count=100):
            raise ConnectionError("redis down")
            yield  # pragma: no cover — makes this an async generator

    # The method swallows the error and logs it; assert the safe default.
    assert await _rl_with(_Boom()).get_fleet_concurrent_count() == 0
