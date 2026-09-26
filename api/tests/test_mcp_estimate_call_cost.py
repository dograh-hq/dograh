from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.mcp_server.tools.cost import estimate_call_cost


@pytest.fixture
def authed_user() -> MagicMock:
    user = MagicMock()
    user.selected_organization_id = 1
    return user


def _patches(user: MagicMock, org: SimpleNamespace | None):
    return (
        patch(
            "api.mcp_server.tools.cost.authenticate_mcp_request",
            AsyncMock(return_value=user),
        ),
        patch(
            "api.mcp_server.tools.cost.db_client.get_organization_by_id",
            AsyncMock(return_value=org),
        ),
    )


@pytest.mark.asyncio
async def test_estimate_uses_the_organization_price_per_second(authed_user: MagicMock):
    auth, org = _patches(authed_user, SimpleNamespace(price_per_second_usd=0.001))

    with auth, org:
        result = await estimate_call_cost(expected_duration_seconds=60)

    assert result == {
        "expected_duration_seconds": 60,
        "price_per_second_usd": 0.001,
        "estimated_total_usd": 0.06,
        "currency": "USD",
    }


@pytest.mark.asyncio
async def test_a_sub_cent_estimate_is_not_rounded_away(authed_user: MagicMock):
    auth, org = _patches(authed_user, SimpleNamespace(price_per_second_usd=0.0005))

    with auth, org:
        result = await estimate_call_cost(expected_duration_seconds=10)

    assert result["estimated_total_usd"] == 0.005


@pytest.mark.asyncio
async def test_sixty_seconds_is_the_default(authed_user: MagicMock):
    auth, org = _patches(authed_user, SimpleNamespace(price_per_second_usd=0.002))

    with auth, org:
        result = await estimate_call_cost()

    assert result["expected_duration_seconds"] == 60
    assert result["estimated_total_usd"] == 0.12


@pytest.mark.asyncio
async def test_a_negative_duration_is_rejected_before_any_lookup(
    authed_user: MagicMock,
):
    auth, org = _patches(authed_user, SimpleNamespace(price_per_second_usd=0.001))

    with auth as auth_mock, org:
        with pytest.raises(HTTPException) as exc:
            await estimate_call_cost(expected_duration_seconds=-1)

    assert exc.value.status_code == 400
    auth_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_organization_without_pricing_gets_a_400(authed_user: MagicMock):
    auth, org = _patches(authed_user, SimpleNamespace(price_per_second_usd=None))

    with auth, org:
        with pytest.raises(HTTPException) as exc:
            await estimate_call_cost(expected_duration_seconds=60)

    assert exc.value.status_code == 400
    assert "pricing" in exc.value.detail


@pytest.mark.asyncio
async def test_a_user_without_a_selected_organization_gets_a_400():
    user = MagicMock()
    user.selected_organization_id = None
    auth, org = _patches(user, None)

    with auth, org as org_mock:
        with pytest.raises(HTTPException) as exc:
            await estimate_call_cost(expected_duration_seconds=60)

    assert exc.value.status_code == 400
    org_mock.assert_not_awaited()
