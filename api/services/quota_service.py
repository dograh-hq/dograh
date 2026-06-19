"""Quota checking service for Dograh credits.

This module provides reusable quota checking functionality that can be used
across different endpoints (WebRTC signaling, telephony, public API triggers).
"""

from dataclasses import dataclass
from typing import Any

from loguru import logger

from api.constants import DEPLOYMENT_MODE
from api.db import db_client
from api.db.models import UserModel
from api.services.configuration.ai_model_configuration import (
    get_effective_ai_model_configuration_for_workflow,
)
from api.services.configuration.registry import ServiceProviders
from api.services.managed_model_services import (
    MPS_CORRELATION_ID_CONTEXT_KEY,
    get_dograh_service_api_key,
    uses_managed_model_services_v2,
)
from api.services.mps_service_key_client import mps_service_key_client

MINIMUM_DOGRAH_CREDITS_FOR_CALL = 0.10

LEGACY_QUOTA_EXCEEDED_MESSAGE = (
    "You have exhausted your trial credits. "
    "Please email founders@dograh.com for additional Dograh credits "
    "or change providers in Models configurations."
)

BILLING_V2_QUOTA_EXCEEDED_MESSAGE = (
    "You have exhausted your Dograh credits. "
    "Please purchase more credits from /billing "
    "or change providers in Models configurations."
)

SERVICE_TOKEN_ORG_MISMATCH_MESSAGE = (
    "The Dograh service token being used is created from another account. "
    "Please create a new service token from the Developers tab and use it in "
    "your model configuration."
)


@dataclass
class QuotaCheckResult:
    """Result of a quota check."""

    has_quota: bool
    error_message: str = ""
    error_code: str = ""


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _insufficient_billing_v2_quota_result() -> QuotaCheckResult:
    return QuotaCheckResult(
        has_quota=False,
        error_code="insufficient_credits",
        error_message=BILLING_V2_QUOTA_EXCEEDED_MESSAGE,
    )


def _insufficient_legacy_quota_result() -> QuotaCheckResult:
    return QuotaCheckResult(
        has_quota=False,
        error_code="quota_exceeded",
        error_message=LEGACY_QUOTA_EXCEEDED_MESSAGE,
    )


def _service_uses_dograh(service: Any) -> bool:
    provider = getattr(service, "provider", None)
    return (
        provider == ServiceProviders.DOGRAH or provider == ServiceProviders.DOGRAH.value
    )


def _dograh_api_keys(user_config: Any) -> set[str]:
    api_keys: set[str] = set()
    for section_name in ("llm", "stt", "tts", "embeddings"):
        service = getattr(user_config, section_name, None)
        if not _service_uses_dograh(service):
            continue
        if hasattr(service, "get_all_api_keys"):
            all_api_keys = [
                api_key
                for api_key in service.get_all_api_keys()
                if isinstance(api_key, str) and api_key
            ]
            if all_api_keys:
                api_keys.update(all_api_keys)
                continue
        api_key = getattr(service, "api_key", None)
        if api_key:
            api_keys.add(api_key)
    return api_keys


def _is_service_key_org_mismatch_error(error: Exception) -> bool:
    response = getattr(error, "response", None)
    if getattr(response, "status_code", None) != 403:
        return False

    detail: Any = None
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = payload.get("detail")
    except Exception:
        detail = None

    if isinstance(detail, str):
        return detail.lower() == "service key organization mismatch"

    response_text = getattr(response, "text", "")
    return "Service key organization mismatch" in response_text


async def _store_run_correlation_id(
    workflow_run_id: int | None,
    correlation_id: str | None,
) -> None:
    if not workflow_run_id or not correlation_id:
        return

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            "Could not store MPS correlation id for missing workflow run {}",
            workflow_run_id,
        )
        return

    initial_context = dict(workflow_run.initial_context or {})
    if initial_context.get(MPS_CORRELATION_ID_CONTEXT_KEY) == correlation_id:
        return

    initial_context[MPS_CORRELATION_ID_CONTEXT_KEY] = correlation_id
    await db_client.update_workflow_run(
        workflow_run_id,
        initial_context=initial_context,
    )


async def _authorize_hosted_workflow_run_start(
    *,
    workflow_owner: UserModel,
    organization_id: int | None,
    workflow_id: int | None,
    workflow_run_id: int | None,
    user_config: Any,
) -> tuple[QuotaCheckResult, bool]:
    """Authorize hosted v2 billing and return whether MPS handled enforcement."""
    if DEPLOYMENT_MODE == "oss" or organization_id is None:
        return QuotaCheckResult(has_quota=True), False

    requires_correlation = bool(
        workflow_run_id and uses_managed_model_services_v2(user_config)
    )
    service_key = (
        get_dograh_service_api_key(user_config) if requires_correlation else None
    )
    if requires_correlation and not service_key:
        return (
            QuotaCheckResult(
                has_quota=False,
                error_code="invalid_service_key",
                error_message=(
                    "You have invalid keys in your model configuration. "
                    "Please validate the service keys."
                ),
            ),
            True,
        )

    try:
        authorization = await mps_service_key_client.authorize_workflow_run_start(
            organization_id=organization_id,
            workflow_run_id=workflow_run_id,
            service_key=service_key,
            require_correlation_id=requires_correlation,
            minimum_credits=MINIMUM_DOGRAH_CREDITS_FOR_CALL,
            created_by=(
                str(workflow_owner.provider_id)
                if workflow_owner.provider_id is not None
                else None
            ),
            metadata={
                "dograh_user_id": str(workflow_owner.id),
                "workflow_id": workflow_id,
            },
        )
    except Exception as e:
        logger.warning(
            "Failed to authorize workflow start with MPS for org {}: {}",
            organization_id,
            e,
        )
        if _is_service_key_org_mismatch_error(e):
            return (
                QuotaCheckResult(
                    has_quota=False,
                    error_code="service_key_org_mismatch",
                    error_message=SERVICE_TOKEN_ORG_MISMATCH_MESSAGE,
                ),
                True,
            )
        return (
            QuotaCheckResult(
                has_quota=False,
                error_code="quota_check_failed",
                error_message="Could not verify Dograh credits. Please try again.",
            ),
            True,
        )

    billing_mode = authorization.get("billing_mode")
    if billing_mode != "v2":
        return QuotaCheckResult(has_quota=True), False

    remaining = _safe_float(authorization.get("remaining_credits"))
    if (
        not authorization.get("allowed", False)
        or remaining < MINIMUM_DOGRAH_CREDITS_FOR_CALL
    ):
        logger.warning(
            "Insufficient Dograh billing v2 credits for org {}: {:.2f} credits remaining",
            organization_id,
            remaining,
        )
        return _insufficient_billing_v2_quota_result(), True

    try:
        await _store_run_correlation_id(
            workflow_run_id,
            authorization.get("correlation_id"),
        )
    except Exception as e:
        logger.error(
            "Failed to store MPS correlation id for workflow_run_id {}: {}",
            workflow_run_id,
            e,
        )
        return (
            QuotaCheckResult(
                has_quota=False,
                error_code="quota_check_failed",
                error_message="Could not verify Dograh credits. Please try again.",
            ),
            True,
        )
    logger.info(
        "Dograh billing v2 run authorization passed for org {}: {:.2f} credits remaining",
        organization_id,
        remaining,
    )
    return QuotaCheckResult(has_quota=True), True


async def _authorize_legacy_dograh_keys(
    *,
    dograh_api_keys: set[str],
    organization_id: int | None,
    workflow_owner: UserModel,
) -> QuotaCheckResult:
    for api_key in dograh_api_keys:
        try:
            usage = await mps_service_key_client.check_service_key_usage(
                api_key,
                organization_id=organization_id,
                created_by=workflow_owner.provider_id,
            )
            remaining = usage.get("remaining_credits", 0.0)

            # Require at least $0.10 for a short call
            if remaining < MINIMUM_DOGRAH_CREDITS_FOR_CALL:
                logger.warning(
                    f"Insufficient Dograh credits for key ...{api_key[-8:]}: "
                    f"${remaining:.2f} remaining"
                )
                return _insufficient_legacy_quota_result()

            logger.info(
                f"Dograh quota check passed for key ...{api_key[-8:]}: "
                f"{remaining:.2f} credits remaining"
            )
        except Exception as e:
            logger.error(f"Failed to check quota for Dograh key: {str(e)}")
            error_str = str(e)
            if "404" in error_str or "not found" in error_str.lower():
                return QuotaCheckResult(
                    has_quota=False,
                    error_code="invalid_service_key",
                    error_message="You have invalid keys in your model configuration. Please validate the service keys.",
                )
            return QuotaCheckResult(
                has_quota=False,
                error_code="quota_check_failed",
                error_message="Could not verify Dograh credits. Please try again.",
            )

    return QuotaCheckResult(has_quota=True)


async def _authorize_oss_managed_v2_correlation(
    *,
    workflow_id: int,
    workflow_run_id: int | None,
    user_config: Any,
) -> QuotaCheckResult:
    if not workflow_run_id or not uses_managed_model_services_v2(user_config):
        return QuotaCheckResult(has_quota=True)

    service_key = get_dograh_service_api_key(user_config)
    if not service_key:
        return QuotaCheckResult(
            has_quota=False,
            error_code="invalid_service_key",
            error_message=(
                "You have invalid keys in your model configuration. "
                "Please validate the service keys."
            ),
        )

    try:
        response = await mps_service_key_client.create_correlation_id(
            service_key=service_key,
            workflow_run_id=workflow_run_id,
        )
        await _store_run_correlation_id(
            workflow_run_id,
            response.get("correlation_id"),
        )
    except Exception as e:
        logger.error(
            "Failed to authorize OSS managed v2 workflow start for workflow {} run {}: {}",
            workflow_id,
            workflow_run_id,
            e,
        )
        return QuotaCheckResult(
            has_quota=False,
            error_code="quota_check_failed",
            error_message="Could not verify Dograh credits. Please try again.",
        )

    return QuotaCheckResult(has_quota=True)


async def authorize_workflow_run_start(
    *,
    workflow_id: int,
    workflow_run_id: int | None = None,
    actor_user: UserModel | None = None,
) -> QuotaCheckResult:
    """Authorize a workflow run before any billable call/text runtime starts.

    The workflow organization is the billing subject for hosted v2. The workflow
    owner is used only to resolve the effective model configuration and legacy
    service-key metadata.
    """
    try:
        workflow = await db_client.get_workflow_by_id(workflow_id)
        if not workflow:
            return QuotaCheckResult(
                has_quota=False,
                error_code="workflow_not_found",
                error_message="Workflow not found",
            )

        actor_org_id = getattr(actor_user, "selected_organization_id", None)
        if actor_org_id is not None and actor_org_id != workflow.organization_id:
            logger.warning(
                "Workflow start authorization denied: actor org {} does not match workflow {} org {}",
                actor_org_id,
                workflow_id,
                workflow.organization_id,
            )
            return QuotaCheckResult(
                has_quota=False,
                error_code="workflow_not_found",
                error_message="Workflow not found",
            )

        workflow_owner = await db_client.get_user_by_id(workflow.user_id)
        if not workflow_owner:
            return QuotaCheckResult(
                has_quota=False,
                error_code="user_not_found",
                error_message="User not found",
            )

        user_config = await get_effective_ai_model_configuration_for_workflow(
            user_id=workflow_owner.id,
            organization_id=workflow.organization_id,
            workflow_configurations=workflow.workflow_configurations,
        )

        if DEPLOYMENT_MODE != "oss":
            hosted_result, hosted_enforced = await _authorize_hosted_workflow_run_start(
                workflow_owner=workflow_owner,
                organization_id=workflow.organization_id,
                workflow_id=workflow.id,
                workflow_run_id=workflow_run_id,
                user_config=user_config,
            )
            if hosted_enforced or not hosted_result.has_quota:
                return hosted_result

        dograh_api_keys = _dograh_api_keys(user_config)
        if not dograh_api_keys:
            return QuotaCheckResult(has_quota=True)

        legacy_result = await _authorize_legacy_dograh_keys(
            dograh_api_keys=dograh_api_keys,
            organization_id=(
                None if DEPLOYMENT_MODE == "oss" else workflow.organization_id
            ),
            workflow_owner=workflow_owner,
        )
        if not legacy_result.has_quota:
            return legacy_result

        if DEPLOYMENT_MODE == "oss":
            return await _authorize_oss_managed_v2_correlation(
                workflow_id=workflow.id,
                workflow_run_id=workflow_run_id,
                user_config=user_config,
            )

        return QuotaCheckResult(has_quota=True)

    except Exception as e:
        logger.error(f"Error during quota check: {str(e)}")
        # On unexpected error, allow the call to proceed
        return QuotaCheckResult(has_quota=True)
