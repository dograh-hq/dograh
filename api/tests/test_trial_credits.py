"""Trial minute ledger gate: unlimited/positive/exhausted, 402, decrement, no-op, fail-safe."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.services import trial_credits
from api.services.trial_credits import (
    assert_has_free_call_seconds,
    consume_free_call_seconds,
    has_free_call_seconds,
)


def _remaining(value):
    return patch.object(
        trial_credits.db_client,
        "get_free_call_seconds_remaining",
        new=AsyncMock(return_value=value),
    )


async def test_unlimited_allows():
    with _remaining(None):  # NULL balance == unlimited
        assert await has_free_call_seconds(1) is True


async def test_positive_allows():
    with _remaining(120):
        assert await has_free_call_seconds(1) is True


async def test_zero_blocks():
    with _remaining(0):
        assert await has_free_call_seconds(1) is False


async def test_assert_raises_402_when_exhausted():
    with _remaining(0), pytest.raises(HTTPException) as exc:
        await assert_has_free_call_seconds(1)
    assert exc.value.status_code == 402


async def test_assert_noop_when_unlimited():
    with _remaining(None):
        await assert_has_free_call_seconds(1)  # must not raise


async def test_consume_decrements_rounded_int():
    dec = AsyncMock()
    with patch.object(trial_credits.db_client, "decrement_free_call_seconds", new=dec):
        await consume_free_call_seconds(1, 12.6)
    dec.assert_awaited_once_with(1, 13)


async def test_consume_noop_for_none_zero_negative():
    dec = AsyncMock()
    with patch.object(trial_credits.db_client, "decrement_free_call_seconds", new=dec):
        await consume_free_call_seconds(1, None)
        await consume_free_call_seconds(1, 0)
        await consume_free_call_seconds(1, -5)
    dec.assert_not_awaited()


async def test_consume_swallows_db_errors():
    with patch.object(
        trial_credits.db_client,
        "decrement_free_call_seconds",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        await consume_free_call_seconds(1, 10)  # must not raise
