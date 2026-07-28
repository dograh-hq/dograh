"""Unit tests for RateLimiter.get_fleet_concurrent_count — the fleet-wide
autoscaling signal.

The count is a single ZCOUNT over the fleet zset (FLEET_CONCURRENT_KEY) that
the acquire/release paths maintain, so these tests fake that one read. The
write side — acquire mirrors a member in, release removes it, scoped slots
never double-count — runs against a real Redis in test_call_concurrency.py.

The behaviors that matter:
  - fresh slots are counted; stale slots (older than stale_call_timeout) are
    excluded by score, so an orphaned call can't keep the metric high and
    block scale-down;
  - an empty fleet reads 0;
  - Redis errors propagate (the endpoint 503s) instead of reading as an idle
    fleet, which would be a scale-to-minimum instruction.
"""

import time
from unittest.mock import AsyncMock

import pytest

from api.services.call_concurrency.rate_limiter import (
    FLEET_CONCURRENT_KEY,
    RateLimiter,
)

_NOW = time.time()


class _FakeRedis:
    """Async Redis stub: ZCOUNT over one in-memory list of member scores."""

    def __init__(self, scores: list[float]):
        self._scores = scores

    async def zcount(self, key: str, min_score, max_score) -> int:
        assert key == FLEET_CONCURRENT_KEY
        lo = float(min_score)
        return sum(1 for s in self._scores if s >= lo)  # max is "+inf"


def _rl_with(redis) -> RateLimiter:
    rl = RateLimiter()
    rl._get_redis = AsyncMock(return_value=redis)  # type: ignore[method-assign]
    return rl


@pytest.mark.asyncio
async def test_counts_fresh_slots():
    redis = _FakeRedis([_NOW, _NOW, _NOW])
    assert await _rl_with(redis).get_fleet_concurrent_count() == 3


@pytest.mark.asyncio
async def test_excludes_stale_slots():
    # 2 fresh + 3 older than the 1200s stale timeout
    redis = _FakeRedis([_NOW, _NOW, _NOW - 5000, _NOW - 5000, _NOW - 5000])
    assert await _rl_with(redis).get_fleet_concurrent_count() == 2


@pytest.mark.asyncio
async def test_empty_fleet_is_zero():
    assert await _rl_with(_FakeRedis([])).get_fleet_concurrent_count() == 0


@pytest.mark.asyncio
async def test_redis_error_propagates():
    class _Boom:
        async def zcount(self, key, min_score, max_score):
            raise ConnectionError("redis down")

    # A failed read must NOT report 0 (an idle fleet scales to minimum); it
    # propagates so the autoscale-metric endpoint can respond 503 and KEDA's
    # HPA holds the current replica count.
    with pytest.raises(ConnectionError):
        await _rl_with(_Boom()).get_fleet_concurrent_count()
