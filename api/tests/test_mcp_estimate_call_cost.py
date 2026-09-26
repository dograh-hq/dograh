from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.mcp_server.server import mcp
from api.mcp_server.tools.cost import estimate_call_cost

_WORKFLOW = SimpleNamespace(id=7)
_PRICED_ORG = SimpleNamespace(id=22, price_per_second_usd=0.001)


@pytest.fixture
def authed_user() -> MagicMock:
    user = MagicMock()
    user.id = 11
    user.selected_organization_id = 22
    return user


def _patches(authed_user, *, workflow=_WORKFLOW, org=_PRICED_ORG):
    """Patch the tool's auth and the two db reads it makes."""
    return (
        patch(
            "api.mcp_server.tools.cost.authenticate_mcp_request",
            AsyncMock(return_value=authed_user),
        ),
        patch(
            "api.mcp_server.tools.cost.db_client.get_workflow",
            AsyncMock(return_value=workflow),
        ),
        patch(
            "api.mcp_server.tools.cost.db_client.get_organization_by_id",
            AsyncMock(return_value=org),
        ),
    )


@pytest.mark.asyncio
async def test_estimate_uses_the_org_price_per_second(authed_user: MagicMock):
    """60s at $0.001/s is $0.06 — the same seconds * price_per_second_usd the
    usage breakdown bills with, available before the call instead of after."""
    auth_p, wf_p, org_p = _patches(authed_user)
    with auth_p, wf_p, org_p:
        result = await estimate_call_cost(workflow_id=7, expected_duration_seconds=60)

    assert result["estimated_total_usd"] == 0.06
    assert result["price_per_second_usd"] == 0.001
    assert result["expected_duration_seconds"] == 60
    assert result["workflow_id"] == 7
    assert result["currency"] == "USD"
    assert result["source"] == "price_per_second_usd"


@pytest.mark.asyncio
async def test_estimate_defaults_to_sixty_seconds(authed_user: MagicMock):
    org = SimpleNamespace(id=22, price_per_second_usd=0.002)

    auth_p, wf_p, org_p = _patches(authed_user, org=org)
    with auth_p, wf_p, org_p:
        result = await estimate_call_cost(workflow_id=7)

    assert result["expected_duration_seconds"] == 60
    assert result["estimated_total_usd"] == 0.12


@pytest.mark.asyncio
async def test_estimate_keeps_sub_cent_calls_visible(authed_user: MagicMock):
    """A 2s call at $0.001/s is $0.002. Rounding to cents would report 0.00,
    which is why the estimate is returned at 6dp."""
    auth_p, wf_p, org_p = _patches(authed_user)
    with auth_p, wf_p, org_p:
        result = await estimate_call_cost(workflow_id=7, expected_duration_seconds=2)

    assert result["estimated_total_usd"] == 0.002


@pytest.mark.asyncio
async def test_estimate_rejects_a_non_positive_duration(authed_user: MagicMock):
    for bad in (0, -1):
        auth_p, wf_p, org_p = _patches(authed_user)
        with auth_p, wf_p, org_p, pytest.raises(HTTPException) as exc:
            await estimate_call_cost(workflow_id=7, expected_duration_seconds=bad)
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_estimate_404s_for_a_workflow_outside_the_org(authed_user: MagicMock):
    """The workflow read is org-scoped, so another tenant's id reads as missing."""
    auth_p, wf_p, org_p = _patches(authed_user, workflow=None)
    with auth_p, wf_p, org_p, pytest.raises(HTTPException) as exc:
        await estimate_call_cost(workflow_id=999)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_estimate_400s_when_the_org_has_no_pricing(authed_user: MagicMock):
    """Matches the daily-breakdown route: pricing-only feature, 400 when unset."""
    unpriced = SimpleNamespace(id=22, price_per_second_usd=None)

    for org in (unpriced, None):
        auth_p, wf_p, org_p = _patches(authed_user, org=org)
        with auth_p, wf_p, org_p, pytest.raises(HTTPException) as exc:
            await estimate_call_cost(workflow_id=7)
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_estimate_call_cost_is_registered_on_the_mcp_server():
    tools = {tool.name for tool in await mcp.list_tools()}
    assert "estimate_call_cost" in tools
