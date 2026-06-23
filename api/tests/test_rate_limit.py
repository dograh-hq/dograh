"""Fixed-window rate limiter: under/over limit, fail-open, disable, IP extraction."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.services import rate_limit
from api.services.rate_limit import client_ip, enforce_rate_limit


class _FakeRedis:
    def __init__(self):
        self.counts: dict = {}
        self.ttls: dict = {}

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, seconds):
        self.ttls[key] = seconds

    async def ttl(self, key):
        return self.ttls.get(key, -1)


def _req(headers=None, host="9.9.9.9"):
    return SimpleNamespace(
        headers={k.lower(): v for k, v in (headers or {}).items()},
        client=SimpleNamespace(host=host),
    )


def test_client_ip_uses_rightmost_xff():
    # Edge proxy appends the real peer last; left entries are spoofable.
    r = _req({"X-Forwarded-For": "1.2.3.4, 10.0.0.1"})
    assert client_ip(r) == "10.0.0.1"


def test_client_ip_falls_back_to_peer():
    # No XFF and a spoofable X-Real-IP is NOT trusted — use the TCP peer.
    assert client_ip(_req({"X-Real-IP": "5.6.7.8"}, host="7.7.7.7")) == "7.7.7.7"


async def test_under_limit_passes():
    fake = _FakeRedis()
    with patch.object(rate_limit, "_get_redis", new=AsyncMock(return_value=fake)):
        for _ in range(5):
            await enforce_rate_limit(bucket="b", identity="ip", limit=5, window_seconds=60)
    assert fake.counts["rl:b:ip"] == 5  # exactly at the limit, no raise


async def test_over_limit_raises_429():
    fake = _FakeRedis()
    with patch.object(rate_limit, "_get_redis", new=AsyncMock(return_value=fake)):
        for _ in range(3):
            await enforce_rate_limit(bucket="b", identity="ip", limit=3, window_seconds=60)
        with pytest.raises(HTTPException) as exc:
            await enforce_rate_limit(bucket="b", identity="ip", limit=3, window_seconds=60)
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


async def test_window_expire_set_once():
    fake = _FakeRedis()
    with patch.object(rate_limit, "_get_redis", new=AsyncMock(return_value=fake)):
        await enforce_rate_limit(bucket="b", identity="ip", limit=5, window_seconds=42)
        assert fake.ttls["rl:b:ip"] == 42


async def test_zero_limit_disables():
    # Must not even touch redis when disabled.
    with patch.object(
        rate_limit, "_get_redis", new=AsyncMock(side_effect=AssertionError("touched redis"))
    ):
        await enforce_rate_limit(bucket="b", identity="ip", limit=0, window_seconds=60)


async def test_fail_open_on_redis_error():
    with patch.object(
        rate_limit, "_get_redis", new=AsyncMock(side_effect=RuntimeError("redis down"))
    ):
        # Should NOT raise — fail open.
        await enforce_rate_limit(bucket="b", identity="ip", limit=1, window_seconds=60)
