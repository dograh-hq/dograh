from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from api.services import quota_service
from api.services.configuration.registry import ServiceProviders
from api.services.managed_model_services import MPS_CORRELATION_ID_CONTEXT_KEY
from api.services.quota_service import QuotaCheckResult

_UNSET = object()


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


def _pinned_run(
    *,
    workflow_id: int = 7,
    workflow_configurations: dict | None = None,
):
    return SimpleNamespace(
        workflow_id=workflow_id,
        definition=SimpleNamespace(
            workflow_configurations=(
                workflow_configurations
                if workflow_configurations is not None
                else {"model_overrides": {}}
            ),
        ),
    )


def _actor():
    return SimpleNamespace(
        id=456,
        provider_id="actor-456",
        selected_organization_id=42,
    )


def _patch_workflow_context(monkeypatch, *, workflow=_UNSET, owner=None):
    workflow_value = _workflow() if workflow is _UNSET else workflow
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow",
        AsyncMock(return_value=workflow_value),
    )
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_by_id",
        AsyncMock(side_effect=AssertionError("quota must not use unscoped workflow")),
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

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
    )

    assert result.has_quota is True
    quota_service.db_client.get_workflow.assert_awaited_once_with(7, organization_id=42)
    get_config.assert_awaited_once_with(
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

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
    )

    assert result.has_quota is False
    assert result.error_code == "insufficient_credits"
    assert "/billing" in result.error_message
    assert "founders@dograh.com" not in result.error_message
    authorize.assert_awaited_once()
    check_usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorize_workflow_run_oss_exhausted_key_blocks_run(
    monkeypatch,
):
    api_key = "mps_sk_12345678"
    get_config = AsyncMock(return_value=_dograh_config(api_key))
    check_usage = AsyncMock(
        return_value={"total_credits_used": 500.0, "remaining_credits": 0.0}
    )

    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "oss")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        get_config,
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "check_service_key_usage",
        check_usage,
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
    )

    assert result.has_quota is False
    assert result.error_code == "quota_exceeded"
    assert "app.dograh.com" in result.error_message
    assert "/billing" not in result.error_message
    check_usage.assert_awaited_once_with(api_key)


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
        "get_workflow_run",
        AsyncMock(return_value=_pinned_run()),
    )
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
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is True
    quota_service.db_client.get_workflow_run.assert_awaited_once_with(
        88, organization_id=42
    )
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
async def test_authorize_workflow_run_service_token_from_wrong_org_prompts_new_token(
    monkeypatch,
):
    api_key = "mps_sk_12345678"
    get_config = AsyncMock(
        return_value=_dograh_config(api_key, managed_service_version=2)
    )
    request = httpx.Request(
        "POST",
        "http://localhost:8004/api/v1/billing/accounts/42/run-authorization",
    )
    response = httpx.Response(
        403,
        json={"detail": "Service key organization mismatch"},
        request=request,
    )
    authorize = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "Failed to authorize MPS workflow run start",
            request=request,
            response=response,
        )
    )

    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run",
        AsyncMock(return_value=_pinned_run()),
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
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is False
    assert result.error_code == "service_key_org_mismatch"
    assert result.error_message == quota_service.SERVICE_TOKEN_ORG_MISMATCH_MESSAGE
    assert "new service token from the Developers tab" in result.error_message
    authorize.assert_awaited_once_with(
        organization_id=42,
        workflow_run_id=88,
        service_key=api_key,
        require_correlation_id=True,
        minimum_credits=quota_service.MINIMUM_DOGRAH_CREDITS_FOR_CALL,
        created_by="provider-123",
        metadata={"dograh_user_id": "123", "workflow_id": 7},
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
        "get_workflow_run",
        AsyncMock(return_value=_pinned_run()),
    )
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
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is True
    hosted_authorize.assert_not_awaited()
    check_usage.assert_awaited_once_with(api_key)
    create_correlation.assert_awaited_once_with(
        service_key=api_key,
        workflow_run_id=88,
    )
    update_workflow_run.assert_awaited_once_with(
        88,
        initial_context={MPS_CORRELATION_ID_CONTEXT_KEY: "oss-corr-123"},
    )


@pytest.mark.asyncio
async def test_authorize_workflow_run_rejects_actor_not_a_member(monkeypatch):
    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "is_user_member_of_organization",
        AsyncMock(return_value=False),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        actor_user=SimpleNamespace(id=456, selected_organization_id=999),
    )

    assert result.has_quota is False
    assert result.error_code == "workflow_not_found"


@pytest.mark.asyncio
async def test_authorize_workflow_run_membership_lookup_error_fails_closed(monkeypatch):
    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "is_user_member_of_organization",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        actor_user=SimpleNamespace(id=456, selected_organization_id=42),
    )

    assert result.has_quota is False
    assert result.error_code == "workflow_not_found"
    quota_service.db_client.get_user_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorize_workflow_run_allows_invited_member(monkeypatch):
    """User invited to an org can start workflows belonging to that org."""
    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "is_user_member_of_organization",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(return_value=_byok_config()),
    )
    hosted_authorize = AsyncMock(return_value=QuotaCheckResult(has_quota=True))
    monkeypatch.setattr(
        quota_service,
        "_authorize_hosted_workflow_run_start",
        hosted_authorize,
    )

    # actor_user.selected_organization_id=999 differs from workflow.organization_id=42,
    # but is_user_member_of_organization returns True so the run should be allowed.
    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        actor_user=SimpleNamespace(id=456, selected_organization_id=999),
    )

    assert result.has_quota is True


@pytest.mark.asyncio
async def test_authorize_workflow_run_requires_organization_scope(monkeypatch):
    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch)
    is_member_mock = AsyncMock()
    monkeypatch.setattr(
        quota_service.db_client,
        "is_user_member_of_organization",
        is_member_mock,
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(return_value=_byok_config()),
    )
    monkeypatch.setattr(
        quota_service,
        "_authorize_hosted_workflow_run_start",
        AsyncMock(return_value=QuotaCheckResult(has_quota=True)),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=None,
        actor_user=SimpleNamespace(id=456),
    )

    assert result.has_quota is False
    assert result.error_code == "workflow_not_found"
    quota_service.db_client.get_workflow.assert_not_awaited()
    is_member_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorize_workflow_run_rejects_workflow_outside_org(monkeypatch):
    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch, workflow=None)
    is_member_mock = AsyncMock()
    monkeypatch.setattr(
        quota_service.db_client,
        "is_user_member_of_organization",
        is_member_mock,
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=99,
        actor_user=SimpleNamespace(id=456),
    )

    assert result.has_quota is False
    assert result.error_code == "workflow_not_found"
    quota_service.db_client.get_workflow.assert_awaited_once_with(7, organization_id=99)
    is_member_mock.assert_not_awaited()
    quota_service.db_client.get_user_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorize_workflow_run_resolves_config_from_pinned_definition(
    monkeypatch,
):
    """The correlation must be minted for the config the run will execute.

    workflow.workflow_configurations is synced to the draft on save, while the
    run executes its pinned definition — if the draft carries a different
    Dograh service key, minting from the workflow column binds the correlation
    to a key the run never uses and MPS rejects every model service call.
    """
    draft_configs = {"model_configuration_v2_override": {"key": "draft"}}
    pinned_configs = {"model_configuration_v2_override": {"key": "published"}}
    workflow = _workflow()
    workflow.workflow_configurations = draft_configs

    get_config = AsyncMock(return_value=_byok_config())

    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch, workflow=workflow)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run",
        AsyncMock(return_value=_pinned_run(workflow_configurations=pinned_configs)),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        get_config,
    )
    monkeypatch.setattr(
        quota_service,
        "_authorize_hosted_workflow_run_start",
        AsyncMock(return_value=QuotaCheckResult(has_quota=True)),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is True
    get_config.assert_awaited_once_with(
        organization_id=42,
        workflow_configurations=pinned_configs,
    )


@pytest.mark.asyncio
async def test_authorize_workflow_run_falls_back_to_workflow_configs_without_definition(
    monkeypatch,
):
    """Legacy runs without a pinned definition keep using the workflow column."""
    get_config = AsyncMock(return_value=_byok_config())

    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run",
        AsyncMock(return_value=SimpleNamespace(workflow_id=7, definition=None)),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        get_config,
    )
    monkeypatch.setattr(
        quota_service,
        "_authorize_hosted_workflow_run_start",
        AsyncMock(return_value=QuotaCheckResult(has_quota=True)),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is True
    get_config.assert_awaited_once_with(
        organization_id=42,
        workflow_configurations={"model_overrides": {}},
    )


@pytest.mark.asyncio
async def test_authorize_workflow_run_denies_when_run_missing(monkeypatch):
    get_config = AsyncMock()

    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        get_config,
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is False
    assert result.error_code == "workflow_run_not_found"
    get_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorize_workflow_run_denies_run_bound_to_other_workflow(monkeypatch):
    get_config = AsyncMock()

    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run",
        AsyncMock(return_value=_pinned_run(workflow_id=999)),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        get_config,
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is False
    assert result.error_code == "workflow_run_not_found"
    get_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorize_workflow_run_fails_closed_on_config_resolution_error(
    monkeypatch,
):
    """A config-resolution bug must deny the run, not fail open (issue #331)."""
    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(side_effect=RuntimeError("configuration resolution bug")),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
    )

    assert result.has_quota is False
    assert result.error_code == "quota_check_failed"


@pytest.mark.asyncio
async def test_authorize_workflow_run_fails_closed_on_user_lookup_error(monkeypatch):
    """A DB read failure on the owner lookup is a 'cannot verify' → denied."""
    get_config = AsyncMock()

    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_user_by_id",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        get_config,
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
    )

    assert result.has_quota is False
    assert result.error_code == "quota_check_failed"
    get_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorize_workflow_run_fails_closed_on_run_lookup_error(monkeypatch):
    """A DB read failure on the run lookup is a 'cannot verify' → denied."""
    get_config = AsyncMock()

    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        get_config,
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is False
    assert result.error_code == "quota_check_failed"
    get_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorize_workflow_run_opens_when_hosted_mps_is_unreachable(
    monkeypatch,
):
    request = httpx.Request(
        "POST",
        "https://services.dograh.com/api/v1/billing/accounts/42/run-authorization",
    )

    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(return_value=_byok_config()),
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "authorize_workflow_run_start",
        AsyncMock(
            side_effect=httpx.ConnectError("connection refused", request=request)
        ),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
    )

    assert result.has_quota is True


@pytest.mark.asyncio
async def test_authorize_workflow_run_fails_closed_on_hosted_mps_http_error(
    monkeypatch,
):
    request = httpx.Request(
        "POST",
        "https://services.dograh.com/api/v1/billing/accounts/42/run-authorization",
    )
    response = httpx.Response(503, request=request)

    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(return_value=_byok_config()),
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "authorize_workflow_run_start",
        AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "MPS unavailable",
                request=request,
                response=response,
            )
        ),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
    )

    assert result.has_quota is False
    assert result.error_code == "quota_check_failed"


@pytest.mark.asyncio
async def test_authorize_workflow_run_fails_closed_on_invalid_mps_url(monkeypatch):
    request = httpx.Request("POST", "ftp://services.dograh.com/run-authorization")

    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "saas")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(return_value=_byok_config()),
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "authorize_workflow_run_start",
        AsyncMock(
            side_effect=httpx.UnsupportedProtocol(
                "Unsupported protocol ftp://",
                request=request,
            )
        ),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
    )

    assert result.has_quota is False
    assert result.error_code == "quota_check_failed"


@pytest.mark.asyncio
async def test_authorize_workflow_run_opens_when_oss_quota_mps_is_unreachable(
    monkeypatch,
):
    request = httpx.Request(
        "GET",
        "https://services.dograh.com/api/v1/service-keys/usage/self",
    )

    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "oss")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(return_value=_dograh_config()),
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "check_service_key_usage",
        AsyncMock(side_effect=httpx.ConnectTimeout("timed out", request=request)),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
    )

    assert result.has_quota is True


@pytest.mark.asyncio
async def test_authorize_workflow_run_fails_closed_on_oss_quota_mps_http_error(
    monkeypatch,
):
    request = httpx.Request(
        "GET",
        "https://services.dograh.com/api/v1/service-keys/usage/self",
    )
    response = httpx.Response(503, request=request)

    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "oss")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(return_value=_dograh_config()),
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "check_service_key_usage",
        AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "MPS unavailable",
                request=request,
                response=response,
            )
        ),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
    )

    assert result.has_quota is False
    assert result.error_code == "quota_check_failed"


@pytest.mark.asyncio
async def test_authorize_workflow_run_opens_when_oss_correlation_mps_is_unreachable(
    monkeypatch,
):
    request = httpx.Request(
        "POST",
        "https://services.dograh.com/api/v1/service-keys/correlation-id/self",
    )

    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "oss")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run",
        AsyncMock(return_value=_pinned_run()),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(return_value=_dograh_config(managed_service_version=2)),
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "check_service_key_usage",
        AsyncMock(return_value={"remaining_credits": 25.0}),
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "create_correlation_id",
        AsyncMock(
            side_effect=httpx.ConnectError("connection refused", request=request)
        ),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is True


@pytest.mark.asyncio
async def test_authorize_workflow_run_fails_closed_when_storing_oss_correlation(
    monkeypatch,
):
    monkeypatch.setattr(quota_service, "DEPLOYMENT_MODE", "oss")
    _patch_workflow_context(monkeypatch)
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run",
        AsyncMock(return_value=_pinned_run()),
    )
    monkeypatch.setattr(
        quota_service.db_client,
        "get_workflow_run_by_id",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )
    monkeypatch.setattr(
        quota_service,
        "get_effective_ai_model_configuration_for_workflow",
        AsyncMock(return_value=_dograh_config(managed_service_version=2)),
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "check_service_key_usage",
        AsyncMock(return_value={"remaining_credits": 25.0}),
    )
    monkeypatch.setattr(
        quota_service.mps_service_key_client,
        "create_correlation_id",
        AsyncMock(return_value={"correlation_id": "oss-corr-123"}),
    )

    result = await quota_service.authorize_workflow_run_start(
        workflow_id=7,
        organization_id=42,
        workflow_run_id=88,
    )

    assert result.has_quota is False
    assert result.error_code == "quota_check_failed"
