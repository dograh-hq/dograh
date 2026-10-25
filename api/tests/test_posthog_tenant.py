from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services import posthog_tenant


@pytest.fixture(autouse=True)
def empty_cache(monkeypatch):
    monkeypatch.setattr(posthog_tenant, "_provider_ids", {})
    monkeypatch.setattr(posthog_tenant, "_identified_accounts", set())


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
async def test_failed_lookup_still_returns_the_deployment(monkeypatch, lookup):
    monkeypatch.setattr(posthog_tenant, "get_posthog", lambda: object())
    lookup.side_effect = RuntimeError("database unavailable")

    assert await posthog_tenant.tenant_properties(42) == {
        "deployment_id": posthog_tenant.DOGRAH_DEPLOYMENT_ID
    }


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


@pytest.mark.asyncio
async def test_tenant_event_joins_the_account_group(monkeypatch, lookup):
    monkeypatch.setattr(posthog_tenant, "get_posthog", lambda: object())
    monkeypatch.setattr(posthog_tenant, "DOGRAH_DEPLOYMENT_ID", "eu")
    captured = []
    monkeypatch.setattr(
        posthog_tenant, "capture_event", lambda **kwargs: captured.append(kwargs)
    )

    await posthog_tenant.capture_tenant_event(
        distinct_id="stack-user-1",
        event="workflow_created",
        organization_id=42,
        properties={"workflow_uuid": "wf-uuid"},
    )

    assert captured == [
        {
            "distinct_id": "stack-user-1",
            "event": "workflow_created",
            "properties": {
                "workflow_uuid": "wf-uuid",
                "deployment_id": "eu",
                "organization_provider_id": "team-1",
            },
            "groups": {"account": "team-1"},
        }
    ]


@pytest.mark.asyncio
async def test_tenant_event_without_a_known_team_has_no_group(monkeypatch, lookup):
    monkeypatch.setattr(posthog_tenant, "get_posthog", lambda: object())
    lookup.return_value = None
    captured = []
    monkeypatch.setattr(
        posthog_tenant, "capture_event", lambda **kwargs: captured.append(kwargs)
    )

    await posthog_tenant.capture_tenant_event(
        distinct_id="stack-user-1", event="call_started", organization_id=None
    )

    assert captured[0]["groups"] == {}
    assert captured[0]["properties"] == {
        "deployment_id": posthog_tenant.DOGRAH_DEPLOYMENT_ID
    }


def test_identify_account_once_per_process(monkeypatch):
    monkeypatch.setattr(posthog_tenant, "get_posthog", lambda: object())
    calls = []
    monkeypatch.setattr(
        posthog_tenant,
        "group_identify",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    posthog_tenant.identify_account("team-1", name="Acme", distinct_id="user-1")
    posthog_tenant.identify_account("team-1", name="Acme", distinct_id="user-2")
    posthog_tenant.identify_account("team-2", distinct_id="user-1")

    assert calls == [
        (
            (
                "account",
                "team-1",
                {"organization_provider_id": "team-1", "name": "Acme"},
            ),
            {"distinct_id": "user-1"},
        ),
        (
            ("account", "team-2", {"organization_provider_id": "team-2"}),
            {"distinct_id": "user-1"},
        ),
    ]


def test_identify_account_skips_without_posthog_or_team(monkeypatch):
    calls = []
    monkeypatch.setattr(
        posthog_tenant,
        "group_identify",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    monkeypatch.setattr(posthog_tenant, "get_posthog", lambda: None)
    posthog_tenant.identify_account("team-1")
    monkeypatch.setattr(posthog_tenant, "get_posthog", lambda: object())
    posthog_tenant.identify_account(None)

    assert calls == []
    # Skipping without PostHog must not mark the account as identified.
    posthog_tenant.identify_account("team-1")
    assert len(calls) == 1
