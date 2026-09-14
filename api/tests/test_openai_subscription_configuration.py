from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from api.routes import user as user_routes
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration import check_validity
from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.masking import mask_user_config
from api.services.configuration.merge import merge_user_configurations
from api.services.configuration.openai_subscription_auth import (
    SubscriptionAuthError,
    SubscriptionAuthSettings,
)
from api.services.configuration.registry import (
    OpenAILiveSubscriptionLLMConfiguration,
    ServiceProviders,
)
from api.services.configuration.resolve import (
    enrich_overrides_with_api_keys,
    resolve_effective_config,
)


def subscription_config():
    return EffectiveAIModelConfiguration.model_validate(
        {
            "is_realtime": True,
            "llm": {"provider": "openai", "api_key": "analysis-key"},
            "realtime": {
                "provider": "openai_live_subscription",
                "api_key": "workflow-key",
            },
        }
    )


def test_subscription_configuration_roundtrip_and_masked_update():
    config = subscription_config()
    assert config.realtime.model == "gpt-live-1-codex"
    assert config.realtime.voice == "cove"
    assert config.realtime.backend_model == "gpt-5.4-mini"
    assert EffectiveAIModelConfiguration.model_validate(config.model_dump()) == config
    masked = mask_user_config(config)
    assert "workflow-key" not in str(masked)
    updated = merge_user_configurations(config, {"realtime": masked["realtime"]})
    assert updated.realtime.api_key == "workflow-key"
    assert updated.llm.api_key == "analysis-key"


@pytest.mark.parametrize(
    "updates",
    [
        {"model": "gpt-live-1"},
        {"voice": "alloy"},
        {"backend_model": ""},
        {"api_key": " "},
        {"api_key": ["valid-key", ""]},
    ],
)
def test_subscription_rejects_invalid_model_voice_or_backend_configuration(updates):
    with pytest.raises(ValidationError):
        OpenAILiveSubscriptionLLMConfiguration(**{"api_key": "workflow-key", **updates})


def test_subscription_override_preserves_backend_key_and_revalidates_model():
    config = subscription_config()
    overrides = enrich_overrides_with_api_keys(
        {
            "realtime": {
                "provider": "openai_live_subscription",
                "backend_model": "custom-backend",
            }
        },
        config,
    )
    effective = resolve_effective_config(config, overrides)
    assert effective.realtime.api_key == "workflow-key"
    assert effective.realtime.backend_model == "custom-backend"
    assert config.realtime.backend_model == "gpt-5.4-mini"
    with pytest.raises(ValidationError):
        resolve_effective_config(config, {"realtime": {"model": "gpt-live-1"}})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code", ["disabled", "organization_mismatch", "self_hosted_only"]
)
async def test_subscription_validation_rejects_ineligible_org_before_api_calls(
    monkeypatch, code
):
    assert_org = Mock(side_effect=SubscriptionAuthError(code))
    monkeypatch.setattr(
        check_validity,
        "SubscriptionAuthService",
        Mock(return_value=SimpleNamespace(assert_organization=assert_org)),
    )
    validator = UserConfigurationValidator()
    validate_key = Mock(return_value=True)
    monkeypatch.setattr(validator, "_check_api_key", validate_key)
    with pytest.raises(ValueError, match=SubscriptionAuthError(code).safe_message):
        await validator.validate(subscription_config(), organization_id=99)
    assert_org.assert_called_once_with(99)
    validate_key.assert_not_called()


@pytest.mark.asyncio
async def test_subscription_validates_only_explicit_backend_api_key(
    monkeypatch, tmp_path
):
    settings = SubscriptionAuthSettings(
        enabled=True, codex_home=tmp_path, organization_id="42"
    )
    monkeypatch.setattr(SubscriptionAuthSettings, "from_env", lambda: settings)
    api_client = Mock()
    monkeypatch.setattr(check_validity.openai, "OpenAI", api_client)
    await UserConfigurationValidator().validate(
        subscription_config(), organization_id=42
    )
    assert [call.kwargs["api_key"] for call in api_client.call_args_list] == [
        "analysis-key",
        "workflow-key",
    ]
    assert not (tmp_path / "auth.json").exists()
    assert ServiceProviders.OPENAI_LIVE_SUBSCRIPTION.value == "openai_live_subscription"


@pytest.fixture
def status_app(monkeypatch):
    redis = AsyncMock()
    redis.__aenter__.return_value = redis
    monkeypatch.setattr(user_routes.Redis, "from_url", Mock(return_value=redis))
    app = FastAPI()
    app.include_router(user_routes.router, prefix="/api/v1")
    app.dependency_overrides[user_routes.get_user] = lambda: SimpleNamespace(
        selected_organization_id=42
    )
    return app, redis


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        "disabled",
        "login_required",
        "ready",
        "refresh_required",
        "reauthentication_required",
        "busy",
        "unavailable",
    ],
)
async def test_subscription_status_route_is_scoped_redacted_and_closes_redis(
    monkeypatch, status_app, state
):
    app, redis = status_app
    status = AsyncMock(
        return_value={
            "status": state,
            "message": "Safe operator action.",
            "access_token": "must-not-leak",
            "path": "/must/not/leak",
        }
    )
    monkeypatch.setattr(
        user_routes,
        "SubscriptionAuthService",
        Mock(return_value=SimpleNamespace(status=status)),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/user/configurations/openai-subscription/status"
        )
    assert response.status_code == 200
    assert response.json() == {"status": state, "message": "Safe operator action."}
    status.assert_awaited_once_with(42)
    redis.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_subscription_status_for_wrong_org_is_forbidden(
    monkeypatch, status_app, tmp_path
):
    app, redis = status_app
    monkeypatch.setattr(
        SubscriptionAuthSettings,
        "from_env",
        lambda: SubscriptionAuthSettings(
            enabled=True, codex_home=tmp_path, organization_id="99"
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/user/configurations/openai-subscription/status"
        )
    assert response.status_code == 403
    assert response.json() == {
        "detail": "Subscription voice is not connected to this organization."
    }
    redis.get.assert_not_awaited()
    redis.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("signed_in", [False, True])
async def test_subscription_status_requires_auth_and_selected_organization(
    status_app, signed_in
):
    app, redis = status_app

    def get_user():
        if not signed_in:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return SimpleNamespace(selected_organization_id=None)

    app.dependency_overrides[user_routes.get_user] = get_user
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/user/configurations/openai-subscription/status"
        )
    assert response.status_code == (400 if signed_in else 401)
    redis.__aenter__.assert_not_awaited()
