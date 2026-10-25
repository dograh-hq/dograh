from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services import posthog_tenant


@pytest.fixture(autouse=True)
def empty_cache(monkeypatch):
    monkeypatch.setattr(posthog_tenant, "_provider_ids", {})


@pytest.fixture
def lookup(monkeypatch):
    mock = AsyncMock(return_value=SimpleNamespace(id=42, provider_id="team-1"))
    monkeypatch.setattr(posthog_tenant.db_client, "get_organization_by_id", mock)
    return mock


@pytest.mark.asyncio
async def test_tenant_properties_name_the_team_and_deployment(monkeypatch, lookup):
    monkeypatch.setattr(posthog_tenant, "get_posthog", lambda: object())
    monkeypatch.setattr(posthog_tenant, "DOGRAH_DEPLOYMENT_ID", "eu")

    assert await posthog_tenant.tenant_properties(42) == {
        "organization_provider_id": "team-1",
        "deployment_id": "eu",
    }
    await posthog_tenant.tenant_properties(42)
    lookup.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_tenant_properties_skip_lookup_without_posthog(monkeypatch, lookup):
    monkeypatch.setattr(posthog_tenant, "get_posthog", lambda: None)

    properties = await posthog_tenant.tenant_properties(42)

    assert properties == {"deployment_id": posthog_tenant.DOGRAH_DEPLOYMENT_ID}
    lookup.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_organization_is_not_cached(monkeypatch, lookup):
    monkeypatch.setattr(posthog_tenant, "get_posthog", lambda: object())
    lookup.return_value = None

    assert (await posthog_tenant.tenant_properties(7))[
        "organization_provider_id"
    ] is None
    await posthog_tenant.tenant_properties(7)
    assert lookup.await_count == 2


def test_tenant_groups_send_only_the_account_group():
    assert posthog_tenant.tenant_groups("team-1") == {"account": "team-1"}
    assert posthog_tenant.tenant_groups(None) == {}
