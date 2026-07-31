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

    async def eval(self, script, key_count, key, *args):
        assert key_count == 1
        stored = self.values.get(key)
        if len(args) == 5:
            supplied, consumed_prefix, reserved_prefix, now, reservation = args
            if not stored or stored.startswith(consumed_prefix):
                return 0
            if stored.startswith(reserved_prefix):
                _, expires_at, reserved_token, _ = stored.split(":", 3)
                if int(expires_at) > now or reserved_token != supplied:
                    return 0
            elif stored != supplied:
                return 0
            self.values[key] = reservation
            return 1

        expected, replacement = args
        if stored != expected:
            return 0
        self.values[key] = replacement
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
    assert await security.reserve_stream_token(7, 11, 13, "wrong") is None
    assert await security.reserve_stream_token(8, 11, 13, "stream-token") is None
    reservation = await security.reserve_stream_token(7, 11, 13, "stream-token")
    assert reservation
    assert await security.reserve_stream_token(7, 11, 13, "stream-token") is None
    assert await security.consume_stream_token(7, 11, 13, reservation) is True
    assert await security.consume_stream_token(7, 11, 13, reservation) is False
    assert await security.reserve_stream_token(7, 11, 13, "stream-token") is None
    assert (
        await security.reserve_stream_token(
            7, 11, 13, TryVoxSecurity.CONSUMED_STREAM_TOKEN_PREFIX
        )
        is None
    )
    assert await security.issue_stream_token(7, 11, 13) is None


@pytest.mark.asyncio
async def test_stream_capability_can_retry_only_after_reservation_release():
    redis = _FakeRedis()
    security = TryVoxSecurity(redis)

    with (
        patch(
            "api.services.telephony.providers.tryvox.security.secrets.token_urlsafe",
            side_effect=[
                "stream-token",
                "first-reservation",
                "blocked-reservation",
                "second-reservation",
            ],
        ),
        patch(
            "api.services.telephony.providers.tryvox.security.time.time",
            return_value=100,
        ),
    ):
        token = await security.issue_stream_token(7, 11, 13)
        first = await security.reserve_stream_token(7, 11, 13, token)
        blocked = await security.reserve_stream_token(7, 11, 13, token)
        replacement = await security.issue_stream_token(7, 11, 13)
        released = await security.release_stream_token(7, 11, 13, first, token)
        second = await security.reserve_stream_token(7, 11, 13, token)

    assert first.endswith(":stream-token:first-reservation")
    assert blocked is None
    assert replacement is None
    assert released is True
    assert second.endswith(":stream-token:second-reservation")
    assert await security.release_stream_token(7, 11, 13, first, token) is False


@pytest.mark.asyncio
async def test_stale_stream_reservation_can_be_reclaimed():
    redis = _FakeRedis()
    security = TryVoxSecurity(redis)

    with (
        patch(
            "api.services.telephony.providers.tryvox.security.secrets.token_urlsafe",
            side_effect=["stream-token", "first", "second"],
        ),
        patch(
            "api.services.telephony.providers.tryvox.security.time.time",
            side_effect=[100, 100, 116, 116],
        ),
    ):
        token = await security.issue_stream_token(7, 11, 13)
        first = await security.reserve_stream_token(7, 11, 13, token)
        second = await security.reserve_stream_token(7, 11, 13, token)

    assert first != second
    assert second.endswith(":stream-token:second")


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
