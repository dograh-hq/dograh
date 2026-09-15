from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from api.routes import organization as organization_routes
from api.routes import user as user_routes
from api.schemas.ai_model_configuration import (
    BYOKRealtimeAIModelConfiguration,
    EffectiveAIModelConfiguration,
    OrganizationAIModelConfigurationV2,
    compile_ai_model_configuration_v2,
)
from api.services.configuration import check_validity
from api.services.configuration.ai_model_configuration import (
    convert_legacy_ai_model_configuration_to_v2,
    merge_ai_model_configuration_v2_secrets,
)
from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.masking import mask_user_config
from api.services.configuration.merge import merge_user_configurations
from api.services.configuration.openai_subscription_auth import (
    SubscriptionAuthError,
    SubscriptionAuthSettings,
)
from api.services.configuration.registry import (
    OpenAILiveSubscriptionLLMConfiguration,
    is_openai_subscription_config,
)
from api.services.configuration.resolve import (
    enrich_overrides_with_api_keys,
    resolve_effective_config,
)


def subscription_config():
    return EffectiveAIModelConfiguration.model_validate(
        {
            "is_realtime": True,
            "realtime": {"provider": "openai_live_subscription"},
        }
    )


def test_subscription_configuration_roundtrip_without_api_keys():
    config = subscription_config()
    assert config.realtime.model == "gpt-live-1-codex"
    assert config.realtime.voice == "cove"
    assert config.realtime.backend_model == "gpt-5.6-luna"
    assert config.llm is None
    assert not hasattr(config.realtime, "api_key")
    assert "api_key" not in config.realtime.model_json_schema()["properties"]
    assert EffectiveAIModelConfiguration.model_validate(config.model_dump()) == config
    masked = mask_user_config(config)
    updated = merge_user_configurations(config, {"realtime": masked["realtime"]})
    assert updated == config
    assert is_openai_subscription_config(updated)
    assert not is_openai_subscription_config(EffectiveAIModelConfiguration())


def test_old_subscription_api_settings_are_removed_without_mutating_input():
    old = {
        "is_realtime": True,
        "llm": {"provider": "openai", "api_key": "old-analysis-key"},
        "stt": {"provider": "dograh", "api_key": "old-managed-key"},
        "tts": {"provider": "dograh", "api_key": "old-managed-key"},
        "managed_service_version": 2,
        "realtime": {
            "provider": "openai_live_subscription",
            "api_key": "old-backend-key",
            "backend_model": "gpt-5.4-mini",
        },
    }
    migrated = EffectiveAIModelConfiguration.model_validate(old)
    assert migrated.llm is migrated.stt is migrated.tts is None
    assert migrated.managed_service_version is None
    assert migrated.realtime.backend_model == "gpt-5.6-luna"
    assert "api_key" not in migrated.realtime.model_dump()
    assert old["realtime"]["api_key"] == "old-backend-key"
    assert old["llm"]["api_key"] == "old-analysis-key"
    v2 = convert_legacy_ai_model_configuration_to_v2(migrated)
    assert v2.mode == "byok"
    assert v2.byok.realtime.llm is None
    assert "old-" not in str(v2.model_dump())


@pytest.mark.parametrize(
    "updates",
    [
        {"model": "gpt-live-1"},
        {"voice": "alloy"},
        {"backend_model": ""},
        {"backend_model": " "},
        {"codex_home": "/not/from/ui"},
    ],
)
def test_subscription_rejects_invalid_model_voice_or_server_owned_fields(updates):
    with pytest.raises(ValidationError):
        OpenAILiveSubscriptionLLMConfiguration(**updates)


def test_subscription_preserves_explicit_custom_reasoning_model():
    config = OpenAILiveSubscriptionLLMConfiguration(
        backend_model="account-specific-model"
    )
    assert config.backend_model == "account-specific-model"


def test_keyless_subscription_config_is_retained_when_realtime_is_disabled():
    config = EffectiveAIModelConfiguration.model_validate(
        {
            "is_realtime": False,
            "realtime": {"provider": "openai_live_subscription"},
        }
    )
    assert config.realtime.model == "gpt-live-1-codex"
    assert not is_openai_subscription_config(config)


@pytest.mark.parametrize("with_provider", [False, True])
def test_subscription_override_removes_api_key_and_llm_and_revalidates_model(
    with_provider,
):
    config = subscription_config()
    realtime = {"backend_model": "custom-backend", "api_key": "stale-key"}
    if with_provider:
        realtime["provider"] = "openai_live_subscription"
    overrides = enrich_overrides_with_api_keys(
        {
            "realtime": realtime,
            "llm": {"provider": "openai", "api_key": "stale-analysis"},
        },
        config,
    )
    assert "llm" not in overrides
    assert "api_key" not in overrides["realtime"]
    effective = resolve_effective_config(config, overrides)
    assert effective.llm is None
    assert not hasattr(effective.realtime, "api_key")
    assert effective.realtime.backend_model == "custom-backend"
    assert config.realtime.backend_model == "gpt-5.6-luna"
    with pytest.raises(ValidationError):
        resolve_effective_config(config, {"realtime": {"model": "gpt-live-1"}})


@pytest.mark.parametrize("provider_override", [False, True])
def test_saved_subscription_override_ignores_incomplete_unused_api_services(
    provider_override,
):
    config = subscription_config()
    overrides = {
        "llm": {"provider": "openai"},
        "stt": {"provider": "deepgram"},
        "tts": {"provider": "elevenlabs"},
    }
    if provider_override:
        overrides["realtime"] = {"provider": "openai_live_subscription"}
    effective = resolve_effective_config(config, overrides)
    assert effective.llm is effective.stt is effective.tts is None
    assert effective.realtime == config.realtime
    assert "llm" in overrides


def test_switching_api_workflow_override_to_subscription_discards_unused_api_services():
    config = EffectiveAIModelConfiguration.model_validate(
        {
            "is_realtime": True,
            "llm": {"provider": "openai", "api_key": "analysis-key"},
            "realtime": {"provider": "openai_realtime", "api_key": "voice-key"},
        }
    )
    effective = resolve_effective_config(
        config, {"realtime": {"provider": "openai_live_subscription"}}
    )
    assert effective.llm is None
    assert not hasattr(effective.realtime, "api_key")
    assert config.llm.api_key == "analysis-key"
    assert config.realtime.api_key == "voice-key"


@pytest.mark.parametrize(
    "legacy_llm", [None, {"provider": "openai", "api_key": "old-key"}]
)
def test_keyless_v2_save_reload_and_merge_does_not_restore_old_llm(legacy_llm):
    payload = {
        "version": 2,
        "mode": "byok",
        "byok": {
            "mode": "realtime",
            "realtime": {
                "realtime": {
                    "provider": "openai_live_subscription",
                    "api_key": "stale-backend",
                },
                "llm": legacy_llm,
            },
        },
    }
    existing = OrganizationAIModelConfigurationV2.model_validate(payload)
    incoming = convert_legacy_ai_model_configuration_to_v2(subscription_config())
    merged = merge_ai_model_configuration_v2_secrets(incoming, existing)
    assert compile_ai_model_configuration_v2(merged) == subscription_config()
    assert "api_key" not in str(merged.model_dump())


def test_other_realtime_providers_still_require_separate_llm_and_api_key():
    with pytest.raises(ValidationError, match="llm configuration is required"):
        BYOKRealtimeAIModelConfiguration(
            realtime={"provider": "openai_realtime", "api_key": "voice-key"}
        )
    with pytest.raises(ValidationError):
        BYOKRealtimeAIModelConfiguration(
            realtime={"provider": "openai_realtime"},
            llm={"provider": "openai", "api_key": "analysis-key"},
        )
    valid = BYOKRealtimeAIModelConfiguration(
        realtime={"provider": "openai_realtime", "api_key": "voice-key"},
        llm={"provider": "openai", "api_key": "analysis-key"},
    )
    assert valid.realtime.api_key == "voice-key"
    assert valid.llm.api_key == "analysis-key"


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
async def test_keyless_subscription_validation_never_calls_api_provider(
    monkeypatch, tmp_path
):
    settings = SubscriptionAuthSettings(
        enabled=True, codex_home=tmp_path, organization_id="42"
    )
    monkeypatch.setattr(SubscriptionAuthSettings, "from_env", lambda: settings)
    api_client = Mock()
    monkeypatch.setattr(check_validity.openai, "OpenAI", api_client)
    result = await UserConfigurationValidator().validate(
        subscription_config(), organization_id=42
    )
    assert result == {"status": [{"model": "all", "message": "ok"}]}
    api_client.assert_not_called()
    assert not (tmp_path / "auth.json").exists()


@pytest.mark.asyncio
async def test_subscription_validates_only_explicit_optional_embeddings(
    monkeypatch, tmp_path
):
    settings = SubscriptionAuthSettings(
        enabled=True, codex_home=tmp_path, organization_id="42"
    )
    monkeypatch.setattr(SubscriptionAuthSettings, "from_env", lambda: settings)
    api_client = Mock()
    monkeypatch.setattr(check_validity.openai, "OpenAI", api_client)
    config = subscription_config().model_copy(update={"embeddings": None})
    config = EffectiveAIModelConfiguration.model_validate(
        {
            **config.model_dump(),
            "embeddings": {"provider": "openai", "api_key": "explicit-embedding-key"},
        }
    )
    await UserConfigurationValidator().validate(config, organization_id=42)
    assert [call.kwargs["api_key"] for call in api_client.call_args_list] == [
        "explicit-embedding-key"
    ]


@pytest.mark.asyncio
async def test_v2_save_accepts_keyless_subscription_and_persists_no_api_key(
    monkeypatch, tmp_path
):
    settings = SubscriptionAuthSettings(
        enabled=True, codex_home=tmp_path, organization_id="42"
    )
    monkeypatch.setattr(SubscriptionAuthSettings, "from_env", lambda: settings)
    monkeypatch.setattr(
        organization_routes,
        "get_organization_ai_model_configuration_v2",
        AsyncMock(return_value=None),
    )
    upsert = AsyncMock()
    response = AsyncMock(return_value={"saved": True})
    monkeypatch.setattr(
        organization_routes, "upsert_organization_ai_model_configuration_v2", upsert
    )
    monkeypatch.setattr(
        organization_routes, "_model_configuration_v2_response", response
    )
    api_client = Mock()
    monkeypatch.setattr(check_validity.openai, "OpenAI", api_client)
    config = convert_legacy_ai_model_configuration_to_v2(subscription_config())
    result = await organization_routes.save_model_configuration_v2(
        config, SimpleNamespace(selected_organization_id=42, provider_id="demo-user")
    )
    assert result == {"saved": True}
    persisted = upsert.await_args.args[1]
    assert persisted.byok.realtime.llm is None
    assert "api_key" not in str(persisted.model_dump())
    api_client.assert_not_called()


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
