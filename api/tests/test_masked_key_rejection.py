from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.user import router
from api.schemas.ai_model_configuration import (
    DograhManagedAIModelConfiguration,
    EffectiveAIModelConfiguration,
    OrganizationAIModelConfigurationV2,
    compile_ai_model_configuration_v2,
)
from api.services.auth.depends import get_user
from api.services.configuration.ai_model_configuration import (
    ResolvedAIModelConfiguration,
)
from api.services.configuration.masking import mask_key
from api.services.configuration.registry import (
    DeepgramSTTConfiguration,
    GoogleVertexLLMConfiguration,
    OpenAILLMService,
    OpenAITTSService,
)


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


REAL_KEY = "sk-real-key-1234567890abcdef"
MASKED_KEY = mask_key(REAL_KEY)  # "**************************cdef"


def _existing_openai_config():
    return EffectiveAIModelConfiguration(
        llm=OpenAILLMService(
            provider="openai",
            api_key=REAL_KEY,
            model="gpt-4.1",
        ),
        tts=OpenAITTSService(
            provider="openai",
            api_key=REAL_KEY,
            model="gpt-4o-mini-tts",
            voice="alloy",
        ),
        stt=DeepgramSTTConfiguration(
            provider="deepgram",
            api_key=REAL_KEY,
            model="nova-3-general",
        ),
    )


@contextmanager
def _patch_config_update(existing_config=None):
    existing_config = existing_config or _existing_openai_config()
    preferences = SimpleNamespace(test_phone_number=None, timezone=None)

    with (
        patch("api.routes.user.db_client") as mock_db,
        patch("api.routes.user.UserConfigurationValidator") as mock_validator,
        patch(
            "api.routes.user.get_resolved_ai_model_configuration",
            new=AsyncMock(
                return_value=ResolvedAIModelConfiguration(
                    effective=existing_config,
                    source="organization_v2",
                )
            ),
        ),
        patch(
            "api.routes.user.upsert_organization_ai_model_configuration_v2",
            new=AsyncMock(),
        ) as upsert_config,
        patch(
            "api.routes.user.get_organization_preferences",
            new=AsyncMock(return_value=preferences),
        ),
    ):
        mock_db.get_organization_by_id = AsyncMock(return_value=None)
        mock_validator.return_value.validate = AsyncMock()
        yield SimpleNamespace(
            db=mock_db,
            validator=mock_validator,
            upsert_config=upsert_config,
        )


class TestMaskedKeyRejection:
    def test_rejects_masked_api_key_on_provider_change(self):
        """Changing provider with a masked API key should return 400."""
        app = _make_test_app()
        client = TestClient(app)

        with _patch_config_update():
            response = client.put(
                "/user/configurations/user",
                json={
                    "llm": {
                        "provider": "google",
                        "api_key": MASKED_KEY,
                        "model": "gemini-3.5-flash",
                    }
                },
            )

            assert response.status_code == 400
            assert "masked" in response.json()["detail"].lower()

    def test_rejects_masked_api_key_in_list(self):
        """A list of API keys containing a masked key should return 400."""
        app = _make_test_app()
        client = TestClient(app)

        with _patch_config_update():
            response = client.put(
                "/user/configurations/user",
                json={
                    "llm": {
                        "provider": "google",
                        "api_key": ["AIzaSyRealKey123456", MASKED_KEY],
                        "model": "gemini-3.5-flash",
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
        with _patch_config_update() as patched:
            response = client.put(
                "/user/configurations/user",
                json={
                    "llm": {
                        "provider": "google",
                        "api_key": new_key,
                        "model": "gemini-3.5-flash",
                    }
                },
            )

            assert response.status_code == 200
            patched.upsert_config.assert_awaited_once()

    def test_allows_same_provider_with_masked_key(self):
        """Same provider with masked key should succeed (merge resolves it)."""
        app = _make_test_app()
        client = TestClient(app)

        with _patch_config_update() as patched:
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
            patched.upsert_config.assert_awaited_once()

    def test_allows_same_provider_with_masked_vertex_credentials(self):
        """Same provider with masked credentials should succeed."""
        app = _make_test_app()
        client = TestClient(app)

        real_credentials = '{"type":"service_account","project_id":"demo-project"}'
        masked_credentials = mask_key(real_credentials)
        existing = EffectiveAIModelConfiguration(
            llm=GoogleVertexLLMConfiguration(
                provider="google_vertex",
                api_key=None,
                model="gemini-3.5-flash",
                project_id="demo-project",
                location="us-east4",
                credentials=real_credentials,
            ),
            tts=OpenAITTSService(
                provider="openai",
                api_key=REAL_KEY,
                model="gpt-4o-mini-tts",
                voice="alloy",
            ),
            stt=DeepgramSTTConfiguration(
                provider="deepgram",
                api_key=REAL_KEY,
                model="nova-3-general",
            ),
        )

        with _patch_config_update(existing) as patched:
            response = client.put(
                "/user/configurations/user",
                json={
                    "llm": {
                        "provider": "google_vertex",
                        "model": "gemini-3.5-flash",
                        "project_id": "demo-project",
                        "location": "us-east4",
                        "credentials": masked_credentials,
                    }
                },
            )

            assert response.status_code == 200
            patched.upsert_config.assert_awaited_once()

    def test_preference_only_update_does_not_validate_or_save_model_config(self):
        """Saving a test phone number through the legacy endpoint must not touch models."""
        app = _make_test_app(selected_organization_id=11)
        client = TestClient(app)
        preferences = SimpleNamespace(test_phone_number=None, timezone=None)

        with (
            patch("api.routes.user.db_client") as mock_db,
            patch("api.routes.user.UserConfigurationValidator") as mock_validator,
            patch(
                "api.routes.user.get_organization_preferences",
                new=AsyncMock(return_value=preferences),
            ),
            patch(
                "api.routes.user.upsert_organization_preferences",
                new=AsyncMock(return_value=preferences),
            ) as upsert_preferences,
            patch(
                "api.routes.user.get_resolved_ai_model_configuration",
                new=AsyncMock(
                    return_value=ResolvedAIModelConfiguration(
                        effective=_existing_openai_config(),
                        source="organization_v2",
                    )
                ),
            ),
        ):
            existing = _existing_openai_config()
            mock_db.get_user_configurations = AsyncMock(return_value=existing)
            mock_db.update_user_configuration = AsyncMock()
            mock_db.get_organization_by_id = AsyncMock(return_value=None)
            mock_validator.return_value.validate = AsyncMock()

            response = client.put(
                "/user/configurations/user",
                json={"test_phone_number": "+15551234567"},
            )

            assert response.status_code == 200
            assert response.json()["test_phone_number"] == "+15551234567"
            mock_db.update_user_configuration.assert_not_called()
            mock_validator.return_value.validate.assert_not_called()
            upsert_preferences.assert_awaited_once()


def _dograh_managed_config():
    dograh = DograhManagedAIModelConfiguration(api_key="mps-secret")
    return compile_ai_model_configuration_v2(
        OrganizationAIModelConfigurationV2(mode="dograh", dograh=dograh)
    )


LLM = {"provider": "openai", "api_key": REAL_KEY, "model": "gpt-4.1"}
EMB = {"provider": "openai", "api_key": REAL_KEY, "model": "text-embedding-3-small"}
RT = {"provider": "google_realtime", "api_key": "g-key", "model": "gemini-live"}


class TestMixedDograhRejection:
    # A partial switch off Dograh used to be saved as Dograh mode (#615).
    @pytest.mark.parametrize(
        "body",
        [
            {"llm": LLM},
            {"is_realtime": True, "realtime": RT, "llm": LLM, "embeddings": EMB},
        ],
    )
    def test_rejects_partial_switch_off_dograh(self, body):
        client = TestClient(_make_test_app())

        with _patch_config_update(_dograh_managed_config()) as mocks:
            response = client.put("/user/configurations/user", json=body)

            assert response.status_code == 422
            assert "still use Dograh" in response.json()["detail"]
            mocks.upsert_config.assert_not_awaited()

    def test_allows_full_switch_off_dograh(self):
        tts = {**LLM, "model": "gpt-4o-mini-tts", "voice": "alloy"}
        stt = {"provider": "deepgram", "api_key": REAL_KEY, "model": "nova-3-general"}
        body = {"llm": LLM, "tts": tts, "stt": stt, "embeddings": EMB}
        client = TestClient(_make_test_app())

        with _patch_config_update(_dograh_managed_config()) as mocks:
            response = client.put("/user/configurations/user", json=body)

            assert response.status_code == 200
            assert mocks.upsert_config.await_args.args[1].mode == "byok"

    def test_allows_switch_from_byok_realtime_to_dograh(self):
        # The merge keeps the stored realtime block, but is_realtime=False
        # makes it inactive, so it must not count as BYOK.
        existing = EffectiveAIModelConfiguration.model_validate(
            {"is_realtime": True, "realtime": RT, "llm": LLM, "embeddings": EMB}
        )
        dograh = {"provider": "dograh", "api_key": "mps-secret", "model": "default"}
        body = {
            "is_realtime": False,
            "realtime": None,
            "llm": dograh,
            "tts": {**dograh, "voice": "default"},
            "stt": {**dograh, "language": "multi"},
            "embeddings": {**dograh, "model": "dograh_embedding_v1"},
        }
        client = TestClient(_make_test_app())

        with _patch_config_update(existing) as mocks:
            response = client.put("/user/configurations/user", json=body)

            assert response.status_code == 200
            assert mocks.upsert_config.await_args.args[1].mode == "dograh"
