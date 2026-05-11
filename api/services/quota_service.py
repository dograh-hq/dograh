"""Quota checking service for Dograh credits.

This module provides reusable quota checking functionality that can be used
across different endpoints (WebRTC signaling, telephony, public API triggers).
"""

from dataclasses import dataclass

from loguru import logger

from api.db import db_client
from api.db.models import UserModel
from api.services.configuration.registry import ServiceProviders
from api.services.configuration.resolve import resolve_effective_config
from api.services.mps_service_key_client import mps_service_key_client


@dataclass
class QuotaCheckResult:
    """Result of a quota check."""

    has_quota: bool
    error_message: str = ""
    error_code: str = ""


async def check_dograh_quota(
    user: UserModel, workflow_id: int | None = None
) -> QuotaCheckResult:
    """Check if user has sufficient Dograh quota for making a call.

    This function checks if the user is using any Dograh services (LLM, STT, TTS)
    and validates that they have sufficient credits remaining.

    When ``workflow_id`` is provided, the workflow's per-workflow
    ``model_overrides`` are merged onto the user's global config so the quota
    check runs against the credentials that will actually be used for the call
    (rather than always falling back to the user's defaults).

    Args:
        user: The user to check quota for
        workflow_id: Optional workflow whose ``model_overrides`` should be
            applied when resolving the effective service config.

    Returns:
        QuotaCheckResult with has_quota=True if user has sufficient quota or
        is not using Dograh services, or has_quota=False with error_message
        if quota is insufficient.
    """
    try:
        # Get user configurations
        user_config = await db_client.get_user_configurations(user.id)

        if workflow_id is not None:
            workflow = await db_client.get_workflow_by_id(workflow_id)
            if workflow:
                model_overrides = (workflow.workflow_configurations or {}).get(
                    "model_overrides"
                )
                if model_overrides:
                    user_config = resolve_effective_config(user_config, model_overrides)

        # Check if user is using any Dograh service
        using_dograh = False
        dograh_api_keys = set()

        if user_config.llm and user_config.llm.provider == ServiceProviders.DOGRAH:
            using_dograh = True
            dograh_api_keys.add(user_config.llm.api_key)

        if user_config.stt and user_config.stt.provider == ServiceProviders.DOGRAH:
            using_dograh = True
            dograh_api_keys.add(user_config.stt.api_key)

        if user_config.tts and user_config.tts.provider == ServiceProviders.DOGRAH:
            using_dograh = True
            dograh_api_keys.add(user_config.tts.api_key)

        # If not using Dograh, quota check passes
        if not using_dograh:
            return QuotaCheckResult(has_quota=True)

        # Check quota for ALL Dograh keys
        for api_key in dograh_api_keys:
            try:
                usage = await mps_service_key_client.check_service_key_usage(
                    api_key, created_by=user.provider_id
                )
                remaining = usage.get("remaining_credits", 0.0)

                # Require at least $0.10 for a short call
                if remaining < 0.10:
                    logger.warning(
                        f"Insufficient Dograh credits for key ...{api_key[-8:]}: "
                        f"${remaining:.2f} remaining"
                    )
                    return QuotaCheckResult(
                        has_quota=False,
                        error_code="quota_exceeded",
                        error_message=(
                            "You have exhausted your trial credits. "
                            "Please email founders@dograh.com for additional Dograh credits "
                            "or change providers in Models configurations."
                        ),
                    )

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

    except Exception as e:
        logger.error(f"Error during quota check: {str(e)}")
        # On unexpected error, allow the call to proceed
        return QuotaCheckResult(has_quota=True)


async def check_dograh_quota_by_user_id(
    user_id: int, workflow_id: int | None = None
) -> QuotaCheckResult:
    """Check Dograh quota by user ID.

    Convenience function that fetches the user and then checks quota. When
    ``workflow_id`` is provided, the workflow's ``model_overrides`` are
    applied so the quota check evaluates the credentials that will actually
    be used for the call.

    Args:
        user_id: The ID of the user to check quota for
        workflow_id: Optional workflow whose per-workflow overrides should
            be applied to the user's config before checking quota.

    Returns:
        QuotaCheckResult with quota status
    """
    user = await db_client.get_user_by_id(user_id)
    if not user:
        return QuotaCheckResult(
            has_quota=False,
            error_message="User not found",
        )
    return await check_dograh_quota(user, workflow_id=workflow_id)
