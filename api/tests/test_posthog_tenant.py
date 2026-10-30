from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services import posthog_tenant


@pytest.fixture(autouse=True)
def empty_cache(monkeypatch):
    monkeypatch.setattr(posthog_tenant, "_provider_ids", {})
    monkeypatch.setattr(posthog_tenant, "_identified_accounts", {})


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


@pytest.fixture
def identified(monkeypatch):
    """Record group_identify calls; each one reports success."""
    calls = []

    def record(*args, **kwargs):
        calls.append((args, kwargs))
        return True

    monkeypatch.setattr(posthog_tenant, "group_identify", record)
    return calls


@pytest.mark.asyncio
async def test_tenant_event_joins_the_account_group(monkeypatch, lookup, identified):
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
    # Accounts reached only through API keys or local auth get a group too.
    assert identified == [
        (
            ("account", "team-1", {"organization_provider_id": "team-1"}),
            {"distinct_id": "stack-user-1"},
        )
    ]


@pytest.mark.asyncio
async def test_tenant_event_for_an_unknown_organization_has_no_group(
    monkeypatch, lookup, identified
):
    monkeypatch.setattr(posthog_tenant, "get_posthog", lambda: object())
    lookup.return_value = None
    captured = []
    monkeypatch.setattr(
        posthog_tenant, "capture_event", lambda **kwargs: captured.append(kwargs)
    )

    await posthog_tenant.capture_tenant_event(
        distinct_id="stack-user-1", event="call_started", organization_id=7
    )

    lookup.assert_awaited_once_with(7)
    assert captured[0]["groups"] == {}
    assert captured[0]["properties"] == {
        "deployment_id": posthog_tenant.DOGRAH_DEPLOYMENT_ID,
        "organization_provider_id": None,
    }
    assert identified == []


@pytest.mark.asyncio
async def test_tenant_event_without_a_person_is_still_sent(
    monkeypatch, lookup, identified
):
    """A call no user started is personless but still counts for the account."""
    monkeypatch.setattr(posthog_tenant, "get_posthog", lambda: object())
    captured = []
    monkeypatch.setattr(
        posthog_tenant, "capture_event", lambda **kwargs: captured.append(kwargs)
    )

    await posthog_tenant.capture_tenant_event(
        distinct_id=None, event="call_started", organization_id=42
    )

    assert captured[0]["distinct_id"] is None
    assert captured[0]["groups"] == {"account": "team-1"}
    # group_identify falls back to its server distinct id when given None.
    assert identified == [
        (
            ("account", "team-1", {"organization_provider_id": "team-1"}),
            {"distinct_id": None},
        )
    ]


def test_identify_account_once_per_process(monkeypatch, identified):
    monkeypatch.setattr(posthog_tenant, "get_posthog", lambda: object())

    posthog_tenant.identify_account("team-1", name="Acme", distinct_id="user-1")
    posthog_tenant.identify_account("team-1", name="Acme", distinct_id="user-2")
    # A caller without the name neither re-sends nor clears it.
    posthog_tenant.identify_account("team-1", distinct_id="user-2")
    posthog_tenant.identify_account("team-2", distinct_id="user-1")

    assert identified == [
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


def test_identify_account_sends_a_renamed_team(monkeypatch, identified):
    monkeypatch.setattr(posthog_tenant, "get_posthog", lambda: object())

    posthog_tenant.identify_account("team-1", distinct_id="user-1")
    posthog_tenant.identify_account("team-1", name="Acme", distinct_id="user-1")
    posthog_tenant.identify_account("team-1", name="Acme Inc", distinct_id="user-1")

    assert [args[2].get("name") for args, _ in identified] == [
        None,
        "Acme",
        "Acme Inc",
    ]


def test_identify_account_retries_after_a_failed_identify(monkeypatch):
    monkeypatch.setattr(posthog_tenant, "get_posthog", lambda: object())
    results = iter([False, True])
    calls = []

    def flaky(*args, **kwargs):
        calls.append(args)
        return next(results)

    monkeypatch.setattr(posthog_tenant, "group_identify", flaky)

    posthog_tenant.identify_account("team-1")
    posthog_tenant.identify_account("team-1")
    posthog_tenant.identify_account("team-1")

    assert len(calls) == 2


def test_identify_account_skips_without_posthog_or_team(monkeypatch, identified):
    monkeypatch.setattr(posthog_tenant, "get_posthog", lambda: None)
    posthog_tenant.identify_account("team-1")
    monkeypatch.setattr(posthog_tenant, "get_posthog", lambda: object())
    posthog_tenant.identify_account(None)

    assert identified == []
    # Skipping without PostHog must not mark the account as identified.
    posthog_tenant.identify_account("team-1")
    assert len(identified) == 1
