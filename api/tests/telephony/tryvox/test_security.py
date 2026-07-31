import asyncio
from unittest.mock import patch

import pytest

from api.services.telephony.providers.tryvox.security import TryVoxSecurity


class _FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, ex, nx):
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.expirations[key] = ex
        return True

    async def eval(self, script, key_count, key, supplied, consumed):
        assert key_count == 1
        stored = self.values.get(key)
        if stored == consumed or stored != supplied:
            return 0
        self.values[key] = consumed
        return 1


@pytest.mark.asyncio
async def test_stream_capability_is_stable_and_single_use():
    redis = _FakeRedis()
    security = TryVoxSecurity(redis)

    with patch(
        "api.services.telephony.providers.tryvox.security.secrets.token_urlsafe",
        return_value="stream-token",
    ):
        first = await security.issue_stream_token(7, 11, 13)
        second = await security.issue_stream_token(7, 11, 13)

    assert first == second == "stream-token"
    assert await security.redeem_stream_token(7, 11, 13, "wrong") is False
    assert await security.redeem_stream_token(8, 11, 13, "stream-token") is False
    assert await security.redeem_stream_token(7, 11, 13, "stream-token") is True
    assert await security.redeem_stream_token(7, 11, 13, "stream-token") is False
    assert (
        await security.redeem_stream_token(
            7, 11, 13, TryVoxSecurity.CONSUMED_STREAM_TOKEN
        )
        is False
    )
    assert await security.issue_stream_token(7, 11, 13) is None


@pytest.mark.asyncio
async def test_callback_claim_is_atomic_and_retry_safe():
    redis = _FakeRedis()
    security = TryVoxSecurity(redis)

    assert await security.claim_callback("acct-1", 13, "123", '{"call":"one"}') is True
    assert await security.claim_callback("acct-1", 13, "123", '{"call":"one"}') is False
    assert await security.claim_callback("acct-1", 14, "123", '{"call":"one"}') is True
    assert await security.claim_callback("acct-2", 13, "123", '{"call":"one"}') is True
    assert await security.claim_callback("acct-1", 13, "123", '{"call":"two"}') is True
    callback_keys = [key for key in redis.values if key.startswith("tryvox:callback:")]
    assert callback_keys
    assert all(
        redis.expirations[key] == TryVoxSecurity.CALLBACK_REPLAY_TTL_SECONDS
        for key in callback_keys
    )


@pytest.mark.asyncio
async def test_concurrent_callback_claim_allows_one_winner():
    security = TryVoxSecurity(_FakeRedis())

    results = await asyncio.gather(
        *[
            security.claim_callback("acct-1", 13, "123", '{"call":"one"}')
            for _ in range(8)
        ]
    )

    assert results.count(True) == 1
    assert results.count(False) == 7
