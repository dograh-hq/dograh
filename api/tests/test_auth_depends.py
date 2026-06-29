from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.auth import depends as auth_depends


@pytest.mark.asyncio
async def test_get_user_initializes_hosted_mps_billing_for_new_org(monkeypatch):
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
    existing_config = SimpleNamespace(llm=object(), tts=None, stt=None)

    ensure_billing = AsyncMock(return_value={"billing_mode": "v2"})
    group_calls = []
    capture_calls = []
    person_calls = []

    monkeypatch.setattr(auth_depends, "AUTH_PROVIDER", "stack")
    monkeypatch.setattr(
        auth_depends.stackauth,
        "get_user",
        AsyncMock(return_value=stack_user),
    )
    monkeypatch.setattr(
        auth_depends.db_client,
        "get_or_create_user_by_provider_id",
        AsyncMock(return_value=(user, False)),
    )
    monkeypatch.setattr(
        auth_depends.db_client,
        "get_or_create_organization_by_provider_id",
        AsyncMock(return_value=(organization, True)),
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
        auth_depends.db_client,
        "get_user_configurations",
        AsyncMock(return_value=existing_config),
    )
    monkeypatch.setattr(
        auth_depends,
        "ensure_hosted_mps_billing_account_v2",
        ensure_billing,
    )
    monkeypatch.setattr(
        auth_depends,
        "group_identify",
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

    result = await auth_depends.get_user(authorization="Bearer token")

    assert result is user
    assert result.selected_organization_id == 42
    ensure_billing.assert_awaited_once_with(42, created_by="stack-user-1")

    assert len(group_calls) == 1
    group_args, group_kwargs = group_calls[0]
    assert group_args == (
        "organization",
        "42",
        {
            "organization_id": 42,
            "organization_provider_id": "team-1",
            "auth_provider": "stack",
            "created_by_provider_id": "stack-user-1",
        },
    )
    assert group_kwargs == {"distinct_id": "stack-user-1"}

    assert len(person_calls) == 1
    person_args, person_kwargs = person_calls[0]
    assert person_args == (
        "stack-user-1",
        {
            "user_id": 7,
            "user_provider_id": "stack-user-1",
            "selected_organization_id": 42,
            "selected_organization_provider_id": "team-1",
        },
    )
    assert person_kwargs == {}

    assert len(capture_calls) == 2
    org_created_args, org_created_kwargs = capture_calls[0]
    assert org_created_args == ()
    assert org_created_kwargs["distinct_id"] == "stack-user-1"
    assert org_created_kwargs["event"] == auth_depends.PostHogEvent.ORGANIZATION_CREATED
    assert org_created_kwargs["groups"] == {"organization": "42"}
    assert org_created_kwargs["properties"] == {
        "organization_id": 42,
        "organization_provider_id": "team-1",
        "auth_provider": "stack",
        "created_by_provider_id": "stack-user-1",
    }

    association_args, association_kwargs = capture_calls[1]
    assert association_args == ()
    assert association_kwargs["distinct_id"] == "stack-user-1"
    assert (
        association_kwargs["event"]
        == auth_depends.PostHogEvent.ORGANIZATION_USER_ASSOCIATED
    )
    assert association_kwargs["groups"] == {"organization": "42"}
    assert association_kwargs["properties"] == {
        "user_id": 7,
        "organization_id": 42,
        "organization_provider_id": "team-1",
        "auth_provider": "stack",
        "organization_was_created": True,
    }


def test_associate_user_with_posthog_org_supports_backfill_arguments(monkeypatch):
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
        organization_ids=[42, 99],
        selected_organization_id=99,
        selected_organization_provider_id="team-99",
    )

    assert person_calls == [
        (
            (
                "stack-user-1",
                {
                    "user_id": 7,
                    "user_provider_id": "stack-user-1",
                    "selected_organization_id": 99,
                    "selected_organization_provider_id": "team-99",
                    "organization_ids": [42, 99],
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
    assert capture_kwargs["groups"] == {"organization": "42"}
    assert capture_kwargs["properties"] == {
        "user_id": 7,
        "organization_id": 42,
        "organization_provider_id": "team-1",
        "auth_provider": "stack",
        "organization_was_created": False,
    }
    assert "backfilled" not in capture_kwargs["properties"]


def test_sync_created_organization_to_posthog_supports_billing_flag(monkeypatch):
    organization = SimpleNamespace(id=42, provider_id="team-1")
    group_calls = []
    capture_calls = []

    monkeypatch.setattr(
        auth_depends,
        "group_identify",
        lambda *args, **kwargs: group_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        auth_depends,
        "capture_event",
        lambda *args, **kwargs: capture_calls.append((args, kwargs)),
    )

    auth_depends._sync_created_organization_to_posthog(
        organization=organization,
        created_by_provider_id="stack-user-1",
        uses_mps_billing_v2=True,
    )

    _, group_kwargs = group_calls[0]
    group_args, _ = group_calls[0]
    assert group_args == (
        "organization",
        "42",
        {
            "organization_id": 42,
            "organization_provider_id": "team-1",
            "auth_provider": "stack",
            "created_by_provider_id": "stack-user-1",
            "uses_mps_billing_v2": True,
        },
    )
    assert group_kwargs == {"distinct_id": "stack-user-1"}

    _, capture_kwargs = capture_calls[0]
    assert capture_kwargs["distinct_id"] == "stack-user-1"
    assert capture_kwargs["properties"]["uses_mps_billing_v2"] is True


def test_sync_posthog_organization_group_properties_has_no_distinct_id(monkeypatch):
    organization = SimpleNamespace(id=42, provider_id="team-1")
    group_calls = []

    monkeypatch.setattr(
        auth_depends,
        "group_identify",
        lambda *args, **kwargs: group_calls.append((args, kwargs)),
    )

    auth_depends._sync_posthog_organization_group_properties(
        organization=organization,
        uses_mps_billing_v2=True,
    )

    assert group_calls == [
        (
            (
                "organization",
                "42",
                {
                    "organization_id": 42,
                    "organization_provider_id": "team-1",
                    "auth_provider": "stack",
                    "uses_mps_billing_v2": True,
                },
            ),
            {},
        )
    ]


def test_sync_posthog_organization_mps_billing_v2_status(monkeypatch):
    group_calls = []

    monkeypatch.setattr(
        auth_depends,
        "group_identify",
        lambda *args, **kwargs: group_calls.append((args, kwargs)),
    )

    auth_depends._sync_posthog_organization_mps_billing_v2_status(
        42,
        uses_mps_billing_v2=True,
    )

    assert group_calls == [
        (
            (
                "organization",
                "42",
                {"uses_mps_billing_v2": True},
            ),
            {},
        )
    ]
