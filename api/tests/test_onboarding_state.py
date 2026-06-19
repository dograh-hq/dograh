from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.user import router
from api.schemas.onboarding_state import OnboardingState, OnboardingStateUpdate
from api.services.auth.depends import get_user


def _make_test_app():
    app = FastAPI()
    app.include_router(router)

    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.is_superuser = False
    mock_user.selected_organization_id = None

    app.dependency_overrides[get_user] = lambda: mock_user
    return app


class TestOnboardingStateUpdateMerge:
    def test_lists_union_without_duplicates(self):
        state = OnboardingState(
            seen_tooltips=["web_call"], completed_actions=["web_call_started"]
        )
        update = OnboardingStateUpdate(
            seen_tooltips=["web_call", "customize_workflow"],
            completed_actions=["welcome_form_completed"],
        )

        merged = update.apply_to(state)

        assert merged.seen_tooltips == ["web_call", "customize_workflow"]
        assert merged.completed_actions == [
            "web_call_started",
            "welcome_form_completed",
        ]

    def test_omitted_fields_preserve_existing_state(self):
        completed_at = datetime(2026, 6, 12, tzinfo=UTC)
        state = OnboardingState(
            completed_at=completed_at, skipped=True, seen_tooltips=["web_call"]
        )

        merged = OnboardingStateUpdate().apply_to(state)

        assert merged.completed_at == completed_at
        assert merged.skipped is True
        assert merged.seen_tooltips == ["web_call"]

    def test_scalars_overwrite_when_supplied(self):
        state = OnboardingState()
        completed_at = datetime(2026, 6, 12, tzinfo=UTC)

        merged = OnboardingStateUpdate(
            completed_at=completed_at, skipped=True
        ).apply_to(state)

        assert merged.completed_at == completed_at
        assert merged.skipped is True


class TestOnboardingStateRoutes:
    def test_get_returns_defaults_when_no_row(self):
        app = _make_test_app()
        client = TestClient(app)

        with patch(
            "api.services.user_onboarding.db_client.get_user_configuration_value",
            new=AsyncMock(return_value=None),
        ):
            response = client.get("/user/onboarding-state")

        assert response.status_code == 200
        body = response.json()
        assert body["completed_at"] is None
        assert body["skipped"] is False
        assert body["seen_tooltips"] == []
        assert body["completed_actions"] == []

    def test_get_returns_defaults_on_invalid_stored_value(self):
        app = _make_test_app()
        client = TestClient(app)

        with patch(
            "api.services.user_onboarding.db_client.get_user_configuration_value",
            new=AsyncMock(return_value={"skipped": "not-a-bool"}),
        ):
            response = client.get("/user/onboarding-state")

        assert response.status_code == 200
        assert response.json()["skipped"] is False

    def test_put_merges_into_stored_state_and_persists(self):
        app = _make_test_app()
        client = TestClient(app)

        existing = {"seen_tooltips": ["web_call"]}
        upsert = AsyncMock(side_effect=lambda user_id, key, value: value)
        with (
            patch(
                "api.services.user_onboarding.db_client.get_user_configuration_value",
                new=AsyncMock(return_value=existing),
            ),
            patch(
                "api.services.user_onboarding.db_client.upsert_user_configuration_value",
                new=upsert,
            ),
        ):
            response = client.put(
                "/user/onboarding-state",
                json={
                    "completed_at": "2026-06-12T00:00:00Z",
                    "seen_tooltips": ["customize_workflow"],
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["seen_tooltips"] == ["web_call", "customize_workflow"]
        assert body["completed_at"] is not None

        upsert.assert_awaited_once()
        user_id, key, stored = upsert.await_args.args
        assert user_id == 1
        assert key == "ONBOARDING"
        assert stored["seen_tooltips"] == ["web_call", "customize_workflow"]
