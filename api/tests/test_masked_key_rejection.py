from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.user import router
from api.schemas.user_configuration import UserConfiguration
from api.services.auth.depends import get_user
from api.services.configuration.masking import mask_key
from api.services.configuration.registry import (
    GoogleLLMService,
    OpenAILLMService,
)


def _make_test_app():
    app = FastAPI()
    app.include_router(router)

    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.is_superuser = False
    mock_user.selected_organization_id = None

    app.dependency_overrides[get_user] = lambda: mock_user
    return app


REAL_KEY = "sk-real-key-1234567890abcdef"
MASKED_KEY = mask_key(REAL_KEY)  # "**************************cdef"


def _existing_openai_config():
    return UserConfiguration(
        llm=OpenAILLMService(
            provider="openai",
            api_key=REAL_KEY,
            model="gpt-4.1",
        )
    )


class TestMaskedKeyRejection:
    def test_rejects_masked_api_key_on_provider_change(self):
        """Changing provider with a masked API key should return 400."""
        app = _make_test_app()
        client = TestClient(app)

        with (
            patch("api.routes.user.db_client") as mock_db,
            patch("api.routes.user.UserConfigurationValidator") as mock_validator,
        ):
            mock_db.get_user_configurations = AsyncMock(
                return_value=_existing_openai_config()
            )
            mock_db.update_user_configuration = AsyncMock(
                side_effect=lambda uid, cfg: cfg
            )
            mock_validator.return_value.validate = AsyncMock()

            response = client.put(
                "/user/configurations/user",
                json={
                    "llm": {
                        "provider": "google",
                        "api_key": MASKED_KEY,
                        "model": "gemini-2.0-flash",
                    }
                },
            )

            assert response.status_code == 400
            assert "masked" in response.json()["detail"].lower()

    def test_rejects_masked_api_key_in_list(self):
        """A list of API keys containing a masked key should return 400."""
        app = _make_test_app()
        client = TestClient(app)

        with (
            patch("api.routes.user.db_client") as mock_db,
            patch("api.routes.user.UserConfigurationValidator") as mock_validator,
        ):
            mock_db.get_user_configurations = AsyncMock(
                return_value=_existing_openai_config()
            )
            mock_db.update_user_configuration = AsyncMock(
                side_effect=lambda uid, cfg: cfg
            )
            mock_validator.return_value.validate = AsyncMock()

            response = client.put(
                "/user/configurations/user",
                json={
                    "llm": {
                        "provider": "google",
                        "api_key": ["AIzaSyRealKey123456", MASKED_KEY],
                        "model": "gemini-2.0-flash",
                    }
                },
            )

            assert response.status_code == 400
            assert "masked" in response.json()["detail"].lower()

    def test_allows_real_api_key(self):
        """A real (unmasked) API key should be accepted."""
        app = _make_test_app()
        client = TestClient(app)

        new_key = "AIzaSyNewRealKey12345678"
        updated = UserConfiguration(
            llm=GoogleLLMService(
                provider="google",
                api_key=new_key,
                model="gemini-2.0-flash",
            )
        )

        with (
            patch("api.routes.user.db_client") as mock_db,
            patch("api.routes.user.UserConfigurationValidator") as mock_validator,
        ):
            mock_db.get_user_configurations = AsyncMock(
                return_value=_existing_openai_config()
            )
            mock_db.update_user_configuration = AsyncMock(return_value=updated)
            mock_validator.return_value.validate = AsyncMock()

            response = client.put(
                "/user/configurations/user",
                json={
                    "llm": {
                        "provider": "google",
                        "api_key": new_key,
                        "model": "gemini-2.0-flash",
                    }
                },
            )

            assert response.status_code == 200

    def test_allows_same_provider_with_masked_key(self):
        """Same provider with masked key should succeed (merge resolves it)."""
        app = _make_test_app()
        client = TestClient(app)

        with (
            patch("api.routes.user.db_client") as mock_db,
            patch("api.routes.user.UserConfigurationValidator") as mock_validator,
        ):
            existing = _existing_openai_config()
            mock_db.get_user_configurations = AsyncMock(return_value=existing)
            mock_db.update_user_configuration = AsyncMock(return_value=existing)
            mock_validator.return_value.validate = AsyncMock()

            response = client.put(
                "/user/configurations/user",
                json={
                    "llm": {
                        "provider": "openai",
                        "api_key": MASKED_KEY,
                        "model": "gpt-4.1",
                    }
                },
            )

            # Merge resolves the masked key back to the real one,
            # so check_for_masked_keys should NOT raise.
            assert response.status_code == 200
