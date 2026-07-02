"""Channel-slot capacity for the from-number pool (mock Redis — no server).

Telephony concurrency is bound by trunk CHANNELS, not by how many caller-id
numbers exist: one number can carry N concurrent calls. The pool therefore
holds ``max_concurrent_calls`` slot members ("number#idx") round-robined over
the configured numbers, and only the bare number may reach the dial path.
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.services.campaign.rate_limiter import RateLimiter


def _limiter_with_mock_redis():
    limiter = RateLimiter()
    redis = AsyncMock()
    return limiter, redis


class TestBareFromNumber:
    def test_strips_slot_suffix(self):
        assert (
            RateLimiter.bare_from_number("+919484959244#3")
            == "+919484959244"
        )

    def test_bare_number_passes_through(self):
        assert (
            RateLimiter.bare_from_number("+919484959244")
            == "+919484959244"
        )

    def test_none_passes_through(self):
        assert RateLimiter.bare_from_number(None) is None


class TestPoolCapacity:
    @pytest.mark.asyncio
    async def test_single_number_gets_default_capacity_slots(self):
        """1 caller-id + default capacity (5) → 5 concurrent slots."""
        limiter, redis = _limiter_with_mock_redis()
        with patch.object(limiter, "_get_redis", AsyncMock(return_value=redis)):
            ok = await limiter.initialize_from_number_pool(
                organization_id=1,
                from_numbers=["+919484959244"],
                telephony_configuration_id=2,
            )

        assert ok is True
        members = redis.zadd.call_args.args[1]
        assert members == {
            "+919484959244#0": 0,
            "+919484959244#1": 0,
            "+919484959244#2": 0,
            "+919484959244#3": 0,
            "+919484959244#4": 0,
        }
        assert redis.zadd.call_args.kwargs.get("nx") is True

    @pytest.mark.asyncio
    async def test_explicit_capacity_round_robins_numbers(self):
        """2 numbers, capacity 5 → 5 total slots balanced across numbers."""
        limiter, redis = _limiter_with_mock_redis()
        with patch.object(limiter, "_get_redis", AsyncMock(return_value=redis)):
            await limiter.initialize_from_number_pool(
                organization_id=1,
                from_numbers=["+91111", "+92222"],
                telephony_configuration_id=2,
                max_concurrent_calls=5,
            )

        members = redis.zadd.call_args.args[1]
        assert len(members) == 5
        assert sum(1 for m in members if m.startswith("+91111#")) == 3
        assert sum(1 for m in members if m.startswith("+92222#")) == 2

    @pytest.mark.asyncio
    async def test_zero_or_unset_capacity_falls_back_to_default(self):
        """0/None means "not configured" → platform default (the request
        schema forbids 0 via ge=1, so 0 can only mean unset)."""
        limiter, redis = _limiter_with_mock_redis()
        with patch.object(limiter, "_get_redis", AsyncMock(return_value=redis)):
            await limiter.initialize_from_number_pool(
                organization_id=1,
                from_numbers=["+91111"],
                telephony_configuration_id=2,
                max_concurrent_calls=0,
            )

        assert len(redis.zadd.call_args.args[1]) == 5

    @pytest.mark.asyncio
    async def test_empty_numbers_still_returns_false(self):
        limiter, redis = _limiter_with_mock_redis()
        with patch.object(limiter, "_get_redis", AsyncMock(return_value=redis)):
            ok = await limiter.initialize_from_number_pool(
                organization_id=1,
                from_numbers=[],
                telephony_configuration_id=2,
                max_concurrent_calls=5,
            )
        assert ok is False
        redis.zadd.assert_not_called()

    @pytest.mark.asyncio
    async def test_pool_key_is_versioned(self):
        """v2 prefix keeps slotted pools apart from stale bare-member pools."""
        key = RateLimiter._from_number_pool_key(7, 3)
        assert key == "from_number_pool:v2:7:3"
