from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services import quota_service
from api.services.configuration.registry import ServiceProviders
from api.services.managed_model_services import MPS_CORRELATION_ID_CONTEXT_KEY


def _dograh_config(
    api_key: str = "mps_sk_12345678",
    *,
    managed_service_version: int = 1,
):
    return SimpleNamespace(
        managed_service_version=managed_service_version,
        llm=SimpleNamespace(provider=ServiceProviders.DOGRAH, api_key=api_key),
        stt=None,
        tts=None,
        embeddings=None,
    )


def _byok_config():
    return SimpleNamespace(
        managed_service_version=2,
        llm=SimpleNamespace(provider="openai", api_key="sk-openai"),
        stt=None,
        tts=None,
        embeddings=None,
    )


def _workflow():
    return SimpleNamespace(
        id=7,
        user_id=123,
        organization_id=42,
        workflow_configurations={"model_overrides": {}},
    )


def _workflow_owner():
    return SimpleNamespace(
        id=123,
        provider_id="provider-123",
    )


def _actor():
    return SimpleNamespace(
        id=456,
        provider_id="actor-456",
        selected_organization_id=42,
    )


def _patch_workflow_context(monkeypatch, *, workflow=None, owner=None):
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_by_id",
        AsyncMock(return_value=workflow or _workflow()),
    )
    monkeypatch.setattr(
        quota_service.db_client,
        "get_user_by_id",
        AsyncMock(return_value=owner or _workflow_owner()),
    )


@pytest.mark.asyncio
async def test_authorize_workflow_run_uses_workflow_org_for_hosted_v2(
    monkeypatch,
):
    get_config = AsyncMock(return_value=_dograh_config())
    authorize = AsyncMock(
        return_value={
            "allowed": True,
            "billing_mode": "v2",
            "remaining_credits": "25.0000",
        }
    )
    check_usage = AsyncMock()

    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        get_config,
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "authorize_workflow_run_start",
        authorize,
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "check_service_key_usage",
        check_usage,
    )

    result = await quota_service.authorize_workflow_run_start(workflow_id=7)

    assert result.has_quota is True
    get_config.assert_awaited_once_with(
        user_id=123,
        organization_id=42,
        workflow_configurations={"model_overrides": {}},
    )
    authorize.assert_awaited_once_with(
        organization_id=42,
        workflow_run_id=None,
        service_key=None,
        require_correlation_id=False,
        minimum_credits=quota_service.MINIMUM_DOGRAH_CREDITS_FOR_CALL,
        created_by="provider-123",
        metadata={"dograh_user_id": "123", "workflow_id": 7},
    )
    check_usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorize_workflow_run_v2_insufficient_credits_prompts_billing(
    monkeypatch,
):
    get_config = AsyncMock(return_value=_byok_config())
    authorize = AsyncMock(
        return_value={
            "allowed": False,
            "billing_mode": "v2",
            "remaining_credits": "0.0000",
            "error": "insufficient_credits",
        }
    )
    check_usage = AsyncMock()

    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        get_config,
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "authorize_workflow_run_start",
        authorize,
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "check_service_key_usage",
        check_usage,
    )

    result = await quota_service.authorize_workflow_run_start(workflow_id=7)

    assert result.has_quota is False
    assert result.error_code == "insufficient_credits"
    assert "/billing" in result.error_message
    assert "founders@dograh.com" not in result.error_message
    authorize.assert_awaited_once()
    check_usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorize_workflow_run_v1_uses_legacy_key_usage(
    monkeypatch,
):
    api_key = "mps_sk_12345678"
    get_config = AsyncMock(return_value=_dograh_config(api_key))
    authorize = AsyncMock(
        return_value={
            "allowed": True,
            "billing_mode": "v1",
            "remaining_credits": "0.0000",
        }
    )
    check_usage = AsyncMock(
        return_value={"total_credits_used": 500.0, "remaining_credits": 0.0}
    )

    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        get_config,
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "authorize_workflow_run_start",
        authorize,
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "check_service_key_usage",
        check_usage,
    )

    result = await quota_service.authorize_workflow_run_start(workflow_id=7)

    assert result.has_quota is False
    assert result.error_code == "quota_exceeded"
    assert "founders@dograh.com" in result.error_message
    assert "/billing" not in result.error_message
    authorize.assert_awaited_once()
    check_usage.assert_awaited_once_with(
        api_key,
        organization_id=42,
        created_by="provider-123",
    )


@pytest.mark.asyncio
async def test_authorize_workflow_run_managed_v2_stores_hosted_correlation(
    monkeypatch,
):
    api_key = "mps_sk_12345678"
    workflow_run = SimpleNamespace(initial_context={"existing": "value"})
    get_config = AsyncMock(
        return_value=_dograh_config(api_key, managed_service_version=2)
    )
    authorize = AsyncMock(
        return_value={
            "allowed": True,
            "billing_mode": "v2",
            "remaining_credits": "25.0000",
            "correlation_id": "mps-corr-123",
        }
    )
    update_workflow_run = AsyncMock()

    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=workflow_run),
    )
    monkeypatch.setattr(
        quota_service.db_client,
        "update_workflow_run",
        update_workflow_run,
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        get_config,
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "authorize_workflow_run_start",
        authorize,
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "check_service_key_usage",
        AsyncMock(),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        workflow_run_id=88,
    )

    assert result.has_quota is True
    authorize.assert_awaited_once_with(
        organization_id=42,
        workflow_run_id=88,
        service_key=api_key,
        require_correlation_id=True,
        minimum_credits=quota_service.MINIMUM_DOGRAH_CREDITS_FOR_CALL,
        created_by="provider-123",
        metadata={"dograh_user_id": "123", "workflow_id": 7},
    )
    update_workflow_run.assert_awaited_once_with(
        88,
        initial_context={
            "existing": "value",
            MPS_CORRELATION_ID_CONTEXT_KEY: "mps-corr-123",
        },
    )


@pytest.mark.asyncio
async def test_authorize_workflow_run_oss_uses_key_paths_not_workflow_org(
    monkeypatch,
):
    api_key = "mps_sk_12345678"
    workflow_run = SimpleNamespace(initial_context={})
    get_config = AsyncMock(
        return_value=_dograh_config(api_key, managed_service_version=2)
    )
    hosted_authorize = AsyncMock()
    check_usage = AsyncMock(
        return_value={"total_credits_used": 1.0, "remaining_credits": 499.0}
    )
    create_correlation = AsyncMock(return_value={"correlation_id": "oss-corr-123"})
    update_workflow_run = AsyncMock()

    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "oss")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=workflow_run),
    )
    monkeypatch.setattr(
        quota_service.db_client,
        "update_workflow_run",
        update_workflow_run,
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        get_config,
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "authorize_workflow_run_start",
        hosted_authorize,
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "check_service_key_usage",
        check_usage,
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "create_correlation_id",
        create_correlation,
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        workflow_run_id=88,
    )

    assert result.has_quota is True
    hosted_authorize.assert_not_awaited()
    check_usage.assert_awaited_once_with(
        api_key,
        organization_id=None,
        created_by="provider-123",
    )
    create_correlation.assert_awaited_once_with(
        service_key=api_key,
        workflow_run_id=88,
    )
    update_workflow_run.assert_awaited_once_with(
        88,
        initial_context={MPS_CORRELATION_ID_CONTEXT_KEY: "oss-corr-123"},
    )


@pytest.mark.asyncio
async def test_authorize_workflow_run_rejects_actor_from_another_org(monkeypatch):
    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch)

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        actor_user=SimpleNamespace(selected_organization_id=999),
    )

    assert result.has_quota is False
    assert result.error_code == "workflow_not_found"
