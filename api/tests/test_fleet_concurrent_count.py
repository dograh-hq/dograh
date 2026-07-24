"""Unit tests for RateLimiter.get_fleet_concurrent_count — the fleet-wide
autoscaling signal (AUTOSCALING_PLAN.md Phase 1).

Fakes the Redis client so no real Redis is needed. The behavior that matters is
that org counters are summed while campaign scope counters are skipped (a scoped
call lives in BOTH keys, so counting scope keys would double-count).
"""

from unittest.mock import AsyncMock

import pytest

from api.services.campaign.rate_limiter import RateLimiter


class _FakeRedis:
    """Minimal async Redis stub: scan_iter + zcard over an in-memory dict."""

    def __init__(self, cards: dict[str, int], *, zcard_raises: bool = False):
        self._cards = cards
        self._zcard_raises = zcard_raises

    async def scan_iter(self, match: str, count: int = 100):
        prefix = match.rstrip("*")
        for key in self._cards:
            if key.startswith(prefix):
                yield key

    async def zcard(self, key: str) -> int:
        if self._zcard_raises:
            raise ConnectionError("redis down")
        return self._cards[key]


def _rl_with(redis) -> RateLimiter:
    rl = RateLimiter()
    rl._get_redis = AsyncMock(return_value=redis)  # type: ignore[method-assign]
    return rl


@pytest.mark.asyncio
async def test_sums_org_keys_and_skips_scope_keys():
    redis = _FakeRedis(
        {
            "concurrent_calls:1": 3,
            "concurrent_calls:22": 5,
            "concurrent_calls:campaign:9": 4,  # scope key — must be excluded
            "concurrent_calls:campaign:staging": 2,  # non-numeric — excluded
        }
    )
    rl = _rl_with(redis)
    # 3 + 5 = 8; the two campaign scope keys are skipped (no double-count).
    assert await rl.get_fleet_concurrent_count() == 8


@pytest.mark.asyncio
async def test_empty_fleet_is_zero():
    rl = _rl_with(_FakeRedis({}))
    assert await rl.get_fleet_concurrent_count() == 0


@pytest.mark.asyncio
async def test_redis_error_returns_zero():
    redis = _FakeRedis({"concurrent_calls:1": 3}, zcard_raises=True)
    rl = _rl_with(redis)
    # The method swallows the error and logs it; we just assert the safe default.
    assert await rl.get_fleet_concurrent_count() == 0
