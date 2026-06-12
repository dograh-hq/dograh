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
    organization = SimpleNamespace(id=42)
    existing_config = SimpleNamespace(llm=object(), tts=None, stt=None)

    ensure_billing = AsyncMock(return_value={"billing_mode": "v2"})

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

    result = await auth_depends.get_user(authorization="Bearer token")

    assert result is user
    assert result.selected_organization_id == 42
    ensure_billing.assert_awaited_once_with(42, created_by="stack-user-1")
