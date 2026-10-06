"""Caller-ID reuse is informational; only organization capacity is a hard cap."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes.campaign import _validate_max_concurrency

ORG_ID = 206
CONFIG_ID = 55


def _limits(*, org_limit: int, from_numbers: int):
    return (
        patch(
            "api.routes.campaign._get_org_concurrent_limit",
            AsyncMock(return_value=org_limit),
        ),
        patch(
            "api.routes.campaign._get_from_numbers_count",
            AsyncMock(return_value=from_numbers),
        ),
    )


@pytest.mark.asyncio
async def test_concurrency_above_the_caller_id_pool_is_allowed_with_a_warning():
    org, pool = _limits(org_limit=50, from_numbers=2)

    with org, pool:
        warnings = await _validate_max_concurrency(10, ORG_ID, CONFIG_ID)

    assert len(warnings) == 1
    message = warnings[0]
    assert "10" in message and "2" in message
    assert "rotate" in message.lower()
    assert "reused" in message.lower()
    assert "queue for" not in message.lower()


@pytest.mark.asyncio
async def test_concurrency_within_the_pool_says_nothing():
    org, pool = _limits(org_limit=50, from_numbers=10)

    with org, pool:
        assert await _validate_max_concurrency(10, ORG_ID, CONFIG_ID) == []


@pytest.mark.asyncio
async def test_an_unknown_pool_says_nothing():
    # No configuration selected yet, or no active numbers on it. Nothing is
    # known about the CLI count, so there is nothing to advise about.
    org, pool = _limits(org_limit=50, from_numbers=0)

    with org, pool:
        assert await _validate_max_concurrency(10, ORG_ID, None) == []


@pytest.mark.asyncio
async def test_the_organization_limit_is_still_refused():
    # This one is capacity the platform agreed to carry, not telephony advice.
    org, pool = _limits(org_limit=5, from_numbers=50)

    with org, pool:
        with pytest.raises(HTTPException) as excinfo:
            await _validate_max_concurrency(10, ORG_ID, CONFIG_ID)

    assert excinfo.value.status_code == 400
    assert "organization limit" in excinfo.value.detail


@pytest.mark.asyncio
async def test_the_organization_limit_is_checked_before_the_pool():
    # A value over both should report the hard failure, not warn about CLIs and
    # then let an impossible campaign through.
    org, pool = _limits(org_limit=5, from_numbers=2)

    with org, pool:
        with pytest.raises(HTTPException):
            await _validate_max_concurrency(10, ORG_ID, CONFIG_ID)
