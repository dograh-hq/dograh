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

        if len(args) == 4:
            pending, active, retiring, ttl = args
            if stored not in (pending, active, retiring):
                return 0
            self.values[key] = retiring
            self.expirations[key] = ttl
            return 1

        if len(args) == 3 and "if not stored" in script:
            processing, completed, ttl = args
            if stored is None:
                self.values[key] = processing
                self.expirations[key] = ttl
                return 1
            return 2 if stored == completed else 0

        if len(args) == 3 and "local stored" in script:
            expected, completed, ttl = args
            if stored not in (None, expected, completed):
                return 0
            self.values[key] = completed
            self.expirations[key] = ttl
            return 1

        if len(args) == 3:
            expected, completed, ttl = args
            if stored != expected:
                return 0
            self.values[key] = completed
            self.expirations[key] = ttl
            return 1

        if len(args) == 1:
            (expected,) = args
            if stored != expected:
                return 0
            del self.values[key]
            self.expirations.pop(key, None)
            return 1

        expected, replacement = args
        if "stored == ARGV[2]" in script and stored == replacement:
            return 1
        if stored != expected:
            return 0
        self.values[key] = replacement
        if "redis.call('SET', KEYS[1], ARGV[2])" in script:
            self.expirations.pop(key, None)
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
            side_effect=[100, 100, 701, 701],
        ),
    ):
        token = await security.issue_stream_token(7, 11, 13)
        first = await security.reserve_stream_token(7, 11, 13, token)
        second = await security.reserve_stream_token(7, 11, 13, token)

    assert first != second
    assert second.endswith(":stream-token:second")


@pytest.mark.asyncio
async def test_live_stream_reservation_survives_slow_setup():
    redis = _FakeRedis()
    security = TryVoxSecurity(redis)

    with (
        patch(
            "api.services.telephony.providers.tryvox.security.secrets.token_urlsafe",
            side_effect=["stream-token", "owner", "retry"],
        ),
        patch(
            "api.services.telephony.providers.tryvox.security.time.time",
            side_effect=[100, 100, 116, 116],
        ),
    ):
        token = await security.issue_stream_token(7, 11, 13)
        reservation = await security.reserve_stream_token(7, 11, 13, token)
        retry = await security.reserve_stream_token(7, 11, 13, token)

    assert reservation
    assert retry is None
    assert await security.consume_stream_token(7, 11, 13, "wrong-reservation") is False
    assert await security.consume_stream_token(7, 11, 13, reservation) is True
    assert await security.release_stream_token(7, 11, 13, reservation, token) is False


@pytest.mark.asyncio
async def test_callback_claim_is_atomic_and_retry_safe():
    redis = _FakeRedis()
    security = TryVoxSecurity(redis)

    state, owner = await security.reserve_callback(
        "acct-1", "status", 13, "123", '{"call":"one"}'
    )
    assert state == "acquired"
    assert owner
    assert (
        await security.reserve_callback("acct-1", "status", 13, "123", '{"call":"one"}')
    )[0] == "in_progress"
    assert await security.finalize_callback(
        "acct-1", "status", 13, "123", '{"call":"one"}', owner
    )
    assert (
        await security.reserve_callback("acct-1", "status", 13, "123", '{"call":"one"}')
    )[0] == "completed"
    assert (
        await security.reserve_callback("acct-1", "status", 13, "124", '{"call":"one"}')
    )[0] == "completed"
    assert (
        await security.reserve_callback("acct-1", "answer", 13, "123", '{"call":"one"}')
    )[0] == "acquired"
    assert (
        await security.reserve_callback("acct-1", "status", 14, "123", '{"call":"one"}')
    )[0] == "acquired"
    assert (
        await security.reserve_callback("acct-2", "status", 13, "123", '{"call":"one"}')
    )[0] == "acquired"
    assert (
        await security.reserve_callback("acct-1", "status", 13, "123", '{"call":"two"}')
    )[0] == "acquired"
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
            security.reserve_callback("acct-1", "status", 13, "123", '{"call":"one"}')
            for _ in range(8)
        ]
    )

    assert [state for state, _ in results].count("acquired") == 1
    assert [state for state, _ in results].count("in_progress") == 7


@pytest.mark.asyncio
async def test_claim_call_id_binds_first_caller_and_rejects_conflicts():
    redis = _FakeRedis()
    security = TryVoxSecurity(redis)

    first = await security.claim_call_id(13, "call-123")
    same = await security.claim_call_id(13, "call-123")
    conflicting = await security.claim_call_id(13, "call-other")
    other_run = await security.claim_call_id(14, "call-other")

    assert first == "call-123"
    assert same == "call-123"
    assert conflicting == "call-123"
    assert other_run == "call-other"


@pytest.mark.asyncio
async def test_call_correlation_is_stable_and_bound_to_run():
    redis = _FakeRedis()
    security = TryVoxSecurity(redis)

    with patch(
        "api.services.telephony.providers.tryvox.security.secrets.token_urlsafe",
        return_value="callback-token",
    ):
        first = await security.issue_call_correlation(13)
        second = await security.issue_call_correlation(13)

    assert first == second == "callback-token"
    assert await security.verify_call_correlation(13, "callback-token") is True
    assert await security.verify_call_correlation(13, "wrong-token") is False
    assert await security.verify_call_correlation(14, "callback-token") is False
    assert await security.verify_call_correlation(13, "") is False

    assert await security.activate_call_correlation(13, "callback-token") is True
    key = security._call_correlation_key(13)
    assert redis.values[key] == "__active__:callback-token"
    assert key not in redis.expirations
    assert await security.verify_call_correlation(13, "callback-token") is True

    assert await security.retire_call_correlation(13, "callback-token") is True
    assert redis.values[key] == "__retiring__:callback-token"
    assert redis.expirations[key] == TryVoxSecurity.CALL_CORRELATION_RETIRE_TTL_SECONDS
    assert await security.verify_call_correlation(13, "callback-token") is True
    assert await security.activate_call_correlation(13, "callback-token") is False


@pytest.mark.asyncio
async def test_safe_completion_preserves_a_newer_owner():
    redis = _FakeRedis()
    security = TryVoxSecurity(redis)
    args = ("acct-1", "status", 13, "123", '{"call":"one"}')

    state, owner = await security.reserve_callback(*args)
    assert state == "acquired"
    assert owner

    callback_key = next(
        key for key in redis.values if key.startswith("tryvox:callback:")
    )
    redis.values[callback_key] = f"{TryVoxSecurity.CALLBACK_PROCESSING_PREFIX}new-owner"

    assert await security.complete_callback_if_unclaimed(*args, owner) is False
    assert redis.values[callback_key].endswith("new-owner")

    del redis.values[callback_key]
    assert await security.complete_callback_if_unclaimed(*args, owner) is True

    assert (await security.reserve_callback(*args))[0] == "completed"


@pytest.mark.asyncio
async def test_callback_release_and_finalize_require_matching_owner():
    security = TryVoxSecurity(_FakeRedis())
    args = ("acct-1", "status", 13, "123", '{"call":"one"}')
    state, owner = await security.reserve_callback(*args)

    assert state == "acquired"
    assert owner
    assert await security.release_callback(*args, "wrong-owner") is False
    assert await security.finalize_callback(*args, "wrong-owner") is False
    assert await security.release_callback(*args, owner) is True

    retry_state, retry_owner = await security.reserve_callback(*args)
    assert retry_state == "acquired"
    assert retry_owner
    assert await security.finalize_callback(*args, retry_owner) is True
    assert await security.release_callback(*args, retry_owner) is False
