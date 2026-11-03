from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.services.auth import depends as auth_depends


def _patch_get_user_dependencies(
    monkeypatch,
    *,
    stack_user,
    user,
    organization,
    org_was_created,
    bootstrap,
    group_calls,
    capture_calls,
    person_calls,
    user_was_created=False,
):
    monkeypatch.setattr(auth_depends, "AUTH_PROVIDER", "stack")
    monkeypatch.setattr(
        auth_depends.stackauth,
        "get_user",
        AsyncMock(return_value=stack_user),
    )
    monkeypatch.setattr(
        auth_depends.db_client,
        "get_or_create_user_by_provider_id",
        AsyncMock(return_value=(user, user_was_created)),
    )
    monkeypatch.setattr(
        auth_depends.db_client,
        "get_or_create_organization_by_provider_id",
        AsyncMock(return_value=(organization, org_was_created)),
    )
    monkeypatch.setattr(
        auth_depends.db_client,
        "add_user_to_organization",
        AsyncMock(),
    )
    monkeypatch.setattr(
        auth_depends.db_client,
        "update_user_selected_organization",
        AsyncMock(),
    )
    monkeypatch.setattr(
        auth_depends,
        "ensure_organization_bootstrapped",
        bootstrap,
    )
    monkeypatch.setattr(auth_depends, "ensure_signup_grant", AsyncMock())
    monkeypatch.setattr(
        auth_depends,
        "identify_account",
        lambda *args, **kwargs: group_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        auth_depends,
        "capture_event",
        lambda *args, **kwargs: capture_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        auth_depends,
        "set_person_properties",
        lambda *args, **kwargs: person_calls.append((args, kwargs)),
    )


@pytest.mark.asyncio
async def test_get_user_bootstraps_new_org(monkeypatch):
    stack_user = {
        "id": "stack-user-1",
        "selected_team_id": "team-1",
        "selected_team": {"id": "team-1", "display_name": "Acme"},
        "primary_email_verified": False,
    }
    user = SimpleNamespace(
        id=7,
        email=None,
        provider_id="stack-user-1",
        selected_organization_id=None,
    )
    organization = SimpleNamespace(id=42, provider_id="team-1")

    bootstrap = AsyncMock(return_value=True)
    group_calls = []
    capture_calls = []
    person_calls = []

    _patch_get_user_dependencies(
        monkeypatch,
        stack_user=stack_user,
        user=user,
        organization=organization,
        org_was_created=True,
        bootstrap=bootstrap,
        group_calls=group_calls,
        capture_calls=capture_calls,
        person_calls=person_calls,
    )

    result = await auth_depends.get_user(authorization="Bearer token")

    assert result is user
    assert result.selected_organization_id == 42
    bootstrap.assert_awaited_once_with(42, created_by="stack-user-1")

    assert group_calls == [
        (("team-1",), {"name": "Acme", "distinct_id": "stack-user-1"})
    ]

    assert len(person_calls) == 1
    person_args, person_kwargs = person_calls[0]
    assert person_args == (
        "stack-user-1",
        {
            "user_provider_id": "stack-user-1",
            "selected_organization_provider_id": "team-1",
        },
    )
    assert person_kwargs == {}

    assert len(capture_calls) == 2
    org_created_args, org_created_kwargs = capture_calls[0]
    assert org_created_args == ()
    assert org_created_kwargs["distinct_id"] == "stack-user-1"
    assert org_created_kwargs["event"] == auth_depends.PostHogEvent.ORGANIZATION_CREATED
    assert org_created_kwargs["groups"] == {"account": "team-1"}
    assert org_created_kwargs["properties"] == {
        "organization_provider_id": "team-1",
        "auth_provider": "stack",
        "created_by_provider_id": "stack-user-1",
        "deployment_id": auth_depends.DOGRAH_DEPLOYMENT_ID,
    }

    association_args, association_kwargs = capture_calls[1]
    assert association_args == ()
    assert association_kwargs["distinct_id"] == "stack-user-1"
    assert (
        association_kwargs["event"]
        == auth_depends.PostHogEvent.ORGANIZATION_USER_ASSOCIATED
    )
    assert association_kwargs["groups"] == {"account": "team-1"}
    assert association_kwargs["properties"] == {
        "organization_provider_id": "team-1",
        "auth_provider": "stack",
        "organization_was_created": True,
        "deployment_id": auth_depends.DOGRAH_DEPLOYMENT_ID,
    }


@pytest.mark.asyncio
async def test_get_user_bootstraps_org_it_did_not_just_create(monkeypatch):
    """Provisioning must not be gated on the create-time flag.

    An organization whose first bootstrap attempt failed is neither newly
    created nor a new selection on the next request. Gating on either left it
    permanently without a model configuration.
    """
    stack_user = {
        "id": "stack-user-1",
        "selected_team_id": "team-1",
        "primary_email_verified": False,
    }
    user = SimpleNamespace(
        id=7,
        email=None,
        provider_id="stack-user-1",
        selected_organization_id=42,
    )
    organization = SimpleNamespace(id=42, provider_id="team-1")
    bootstrap = AsyncMock(return_value=True)

    _patch_get_user_dependencies(
        monkeypatch,
        stack_user=stack_user,
        user=user,
        organization=organization,
        org_was_created=False,
        bootstrap=bootstrap,
        group_calls=[],
        capture_calls=[],
        person_calls=[],
    )

    result = await auth_depends.get_user(authorization="Bearer token")

    assert result is user
    bootstrap.assert_awaited_once_with(42, created_by="stack-user-1")


@pytest.mark.asyncio
async def test_get_user_identifies_the_group_of_an_existing_account(monkeypatch):
    """Accounts created before the group existed get it on their next request."""
    stack_user = {
        "id": "stack-user-1",
        "selected_team_id": "team-1",
        "primary_email_verified": False,
    }
    user = SimpleNamespace(
        id=7, email=None, provider_id="stack-user-1", selected_organization_id=42
    )
    group_calls = []
    capture_calls = []

    _patch_get_user_dependencies(
        monkeypatch,
        stack_user=stack_user,
        user=user,
        organization=SimpleNamespace(id=42, provider_id="team-1"),
        org_was_created=False,
        bootstrap=AsyncMock(return_value=True),
        group_calls=group_calls,
        capture_calls=capture_calls,
        person_calls=[],
    )

    await auth_depends.get_user(authorization="Bearer token")

    assert group_calls == [(("team-1",), {"name": None, "distinct_id": "stack-user-1"})]
    assert capture_calls == []


@pytest.mark.asyncio
async def test_signed_up_names_the_team_and_deployment(monkeypatch):
    stack_user = {
        "id": "stack-user-1",
        "selected_team_id": "team-1",
        "primary_email_verified": False,
    }
    user = SimpleNamespace(
        id=7, email=None, provider_id="stack-user-1", selected_organization_id=42
    )
    capture_calls = []

    _patch_get_user_dependencies(
        monkeypatch,
        stack_user=stack_user,
        user=user,
        organization=SimpleNamespace(id=42, provider_id="team-1"),
        org_was_created=False,
        bootstrap=AsyncMock(return_value=True),
        group_calls=[],
        capture_calls=capture_calls,
        person_calls=[],
        user_was_created=True,
    )

    await auth_depends.get_user(authorization="Bearer token")

    [(_, signed_up)] = capture_calls
    assert signed_up["event"] == auth_depends.PostHogEvent.SIGNED_UP
    assert signed_up["distinct_id"] == "stack-user-1"
    assert signed_up["properties"] == {
        "auth_provider": "stack",
        "organization_provider_id": "team-1",
        "deployment_id": auth_depends.DOGRAH_DEPLOYMENT_ID,
    }
    # Starts the welcome-email workflow, so it carries no $groups.
    assert "groups" not in signed_up


@pytest.mark.asyncio
async def test_get_user_succeeds_when_bootstrap_cannot_complete(monkeypatch):
    """An unprovisioned org must not lock a legitimate user out of the API."""
    stack_user = {
        "id": "stack-user-1",
        "selected_team_id": "team-1",
        "primary_email_verified": False,
    }
    user = SimpleNamespace(
        id=7,
        email=None,
        provider_id="stack-user-1",
        selected_organization_id=None,
    )
    organization = SimpleNamespace(id=42, provider_id="team-1")

    _patch_get_user_dependencies(
        monkeypatch,
        stack_user=stack_user,
        user=user,
        organization=organization,
        org_was_created=True,
        bootstrap=AsyncMock(return_value=False),
        group_calls=[],
        capture_calls=[],
        person_calls=[],
    )

    result = await auth_depends.get_user(authorization="Bearer token")

    assert result is user
    assert result.selected_organization_id == 42


def _patch_oss_auth_dependencies(monkeypatch, *, user, bootstrap):
    monkeypatch.setattr(auth_depends, "AUTH_PROVIDER", "local")
    monkeypatch.setattr(auth_depends, "decode_jwt_token", lambda token: {"sub": "7"})
    monkeypatch.setattr(
        auth_depends.db_client,
        "get_user_by_id",
        AsyncMock(return_value=user),
    )
    monkeypatch.setattr(auth_depends, "ensure_organization_bootstrapped", bootstrap)


@pytest.mark.asyncio
async def test_oss_auth_bootstraps_existing_organization(monkeypatch):
    """An OSS org that predates a bootstrap step must still be provisioned.

    OSS users sign up exactly once, so a deployment that upgrades to a release
    adding managed SIP has no signup left to run. Bootstrap has to be reachable
    from an ordinary authenticated request or those orgs never get provisioned.
    """
    user = SimpleNamespace(id=7, provider_id="oss-user-1", selected_organization_id=42)
    bootstrap = AsyncMock(return_value=True)

    _patch_oss_auth_dependencies(monkeypatch, user=user, bootstrap=bootstrap)

    result = await auth_depends.get_user(authorization="Bearer token")

    assert result is user
    bootstrap.assert_awaited_once_with(42, created_by="oss-user-1")


@pytest.mark.asyncio
async def test_oss_auth_does_not_mask_bootstrap_error_as_invalid_token(monkeypatch):
    """A provisioning error must not surface as an expired token.

    The token decoded fine; telling the user to log in again would send them
    chasing an auth problem they do not have.
    """
    user = SimpleNamespace(id=7, provider_id="oss-user-1", selected_organization_id=42)
    bootstrap = AsyncMock(side_effect=RuntimeError("database unavailable"))

    _patch_oss_auth_dependencies(monkeypatch, user=user, bootstrap=bootstrap)

    with pytest.raises(RuntimeError):
        await auth_depends.get_user(authorization="Bearer token")


@pytest.mark.asyncio
async def test_oss_auth_skips_bootstrap_without_organization(monkeypatch):
    user = SimpleNamespace(
        id=7, provider_id="oss-user-1", selected_organization_id=None
    )
    bootstrap = AsyncMock(return_value=True)

    _patch_oss_auth_dependencies(monkeypatch, user=user, bootstrap=bootstrap)

    result = await auth_depends.get_user(authorization="Bearer token")

    assert result is user
    bootstrap.assert_not_awaited()


@pytest.mark.asyncio
async def test_oss_auth_rejects_unknown_user(monkeypatch):
    bootstrap = AsyncMock(return_value=True)
    _patch_oss_auth_dependencies(monkeypatch, user=None, bootstrap=bootstrap)

    with pytest.raises(HTTPException) as exc_info:
        await auth_depends.get_user(authorization="Bearer token")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "User not found"
    bootstrap.assert_not_awaited()


def test_associate_user_with_posthog_org_uses_selected_provider_id(monkeypatch):
    user = SimpleNamespace(
        id=7,
        email="user@example.com",
        provider_id="stack-user-1",
        selected_organization_id=99,
    )
    organization = SimpleNamespace(id=42, provider_id="team-1")
    person_calls = []
    capture_calls = []

    monkeypatch.setattr(
        auth_depends,
        "set_person_properties",
        lambda *args, **kwargs: person_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        auth_depends,
        "capture_event",
        lambda *args, **kwargs: capture_calls.append((args, kwargs)),
    )

    auth_depends._associate_user_with_posthog_organization(
        user=user,
        organization=organization,
        user_distinct_id="stack-user-1",
        org_was_created=False,
        selected_organization_provider_id="team-99",
    )

    assert person_calls == [
        (
            (
                "stack-user-1",
                {
                    "user_provider_id": "stack-user-1",
                    "selected_organization_provider_id": "team-99",
                    "email": "user@example.com",
                },
            ),
            {},
        )
    ]

    assert len(capture_calls) == 1
    _, capture_kwargs = capture_calls[0]
    assert capture_kwargs["distinct_id"] == "stack-user-1"
    assert (
        capture_kwargs["event"]
        == auth_depends.PostHogEvent.ORGANIZATION_USER_ASSOCIATED
    )
    assert capture_kwargs["groups"] == {"account": "team-1"}
    assert capture_kwargs["properties"] == {
        "organization_provider_id": "team-1",
        "auth_provider": "stack",
        "organization_was_created": False,
        "deployment_id": auth_depends.DOGRAH_DEPLOYMENT_ID,
    }
    assert "backfilled" not in capture_kwargs["properties"]


def test_sync_created_organization_to_posthog_with_provider_id(monkeypatch):
    organization = SimpleNamespace(id=42, provider_id="team-1")
    capture_calls = []

    monkeypatch.setattr(
        auth_depends,
        "capture_event",
        lambda *args, **kwargs: capture_calls.append((args, kwargs)),
    )

    auth_depends._sync_created_organization_to_posthog(
        organization=organization,
        created_by_provider_id="stack-user-1",
    )

    _, capture_kwargs = capture_calls[0]
    assert capture_kwargs["distinct_id"] == "stack-user-1"
    assert capture_kwargs["groups"] == {"account": "team-1"}
    assert capture_kwargs["properties"] == {
        "organization_provider_id": "team-1",
        "auth_provider": "stack",
        "created_by_provider_id": "stack-user-1",
        "deployment_id": auth_depends.DOGRAH_DEPLOYMENT_ID,
    }


def test_sync_created_organization_without_provider_id_sends_no_group(
    monkeypatch,
):
    capture_calls = []
    monkeypatch.setattr(
        auth_depends,
        "capture_event",
        lambda *args, **kwargs: capture_calls.append((args, kwargs)),
    )

    auth_depends._sync_created_organization_to_posthog(
        organization=SimpleNamespace(id=42, provider_id=None),
        created_by_provider_id="stack-user-1",
    )

    assert capture_calls[0][1]["groups"] == {}
