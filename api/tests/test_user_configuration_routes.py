"""The user configuration form carries organization preferences only.

Model settings live in the catalog; model fields an old client still sends
with the form are ignored rather than written anywhere.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.user import router
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.auth.depends import get_user
from api.services.configuration.ai_model_configuration import (
    ResolvedAIModelConfiguration,
)
from api.services.configuration.registry import OpenAILLMService

REAL_KEY = "sk-real-key-1234567890abcdef"


def _make_test_app(selected_organization_id=11):
    app = FastAPI()
    app.include_router(router)
    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.provider_id = "provider-1"
    mock_user.is_superuser = False
    mock_user.selected_organization_id = selected_organization_id
    app.dependency_overrides[get_user] = lambda: mock_user
    return app


def _resolved():
    return ResolvedAIModelConfiguration(
        effective=EffectiveAIModelConfiguration(
            llm=OpenAILLMService(provider="openai", api_key=REAL_KEY, model="gpt-4.1")
        ),
        source="organization_v3",
    )


def test_preferences_are_saved_and_submitted_model_fields_are_ignored():
    client = TestClient(_make_test_app())
    preferences = SimpleNamespace(test_phone_number=None, timezone=None)
    with (
        patch("api.routes.user.db_client") as mock_db,
        patch(
            "api.routes.user.get_resolved_ai_model_configuration",
            new=AsyncMock(return_value=_resolved()),
        ),
        patch(
            "api.routes.user.get_organization_preferences",
            new=AsyncMock(return_value=preferences),
        ),
        patch(
            "api.routes.user.upsert_organization_preferences",
            new=AsyncMock(return_value=preferences),
        ) as upsert_preferences,
    ):
        mock_db.get_organization_by_id = AsyncMock(return_value=None)
        response = client.put(
            "/user/configurations/user",
            json={
                "test_phone_number": "+15551234567",
                "llm": {"provider": "openai", "api_key": "sk-new", "model": "gpt-5"},
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["test_phone_number"] == "+15551234567"
    assert preferences.test_phone_number == "+15551234567"
    upsert_preferences.assert_awaited_once()
    # The response shows the catalog's current, masked setup, not the submission.
    assert body["llm"]["model"] == "gpt-4.1"
    assert body["llm"]["api_key"] != "sk-new"
    assert body["llm"]["api_key"].endswith(REAL_KEY[-4:])
    assert "***" in body["llm"]["api_key"]


def test_preference_only_update_does_not_require_model_settings():
    client = TestClient(_make_test_app())
    preferences = SimpleNamespace(test_phone_number=None, timezone=None)
    with (
        patch("api.routes.user.db_client") as mock_db,
        patch(
            "api.routes.user.get_resolved_ai_model_configuration",
            new=AsyncMock(
                return_value=ResolvedAIModelConfiguration(
                    effective=EffectiveAIModelConfiguration(), source="empty"
                )
            ),
        ),
        patch(
            "api.routes.user.get_organization_preferences",
            new=AsyncMock(return_value=preferences),
        ),
        patch(
            "api.routes.user.upsert_organization_preferences",
            new=AsyncMock(return_value=preferences),
        ) as upsert_preferences,
    ):
        mock_db.get_organization_by_id = AsyncMock(return_value=None)
        response = client.put(
            "/user/configurations/user", json={"timezone": "Europe/Rome"}
        )

    assert response.status_code == 200
    assert response.json()["timezone"] == "Europe/Rome"
    assert preferences.timezone == "Europe/Rome"
    upsert_preferences.assert_awaited_once()
