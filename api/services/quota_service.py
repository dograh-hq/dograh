"""Quota checking service for Dograh credits.

This module provides reusable quota checking functionality that can be used
across different endpoints (WebRTC signaling, telephony, public API triggers).
"""

from dataclasses import dataclass

from loguru import logger

from api.db import db_client
from api.db.models import UserModel
from api.services.configuration.registry import ServiceProviders
from api.services.mps_service_key_client import mps_service_key_client


@dataclass
class QuotaCheckResult:
    """Result of a quota check."""

    has_quota: bool
    error_message: str = ""


async def check_dograh_quota(user: UserModel) -> QuotaCheckResult:
    """Check if user has sufficient Dograh quota for making a call.

    This function checks if the user is using any Dograh services (LLM, STT, TTS)
    and validates that they have sufficient credits remaining.

    Args:
        user: The user to check quota for

    Returns:
        QuotaCheckResult with has_quota=True if user has sufficient quota or
        is not using Dograh services, or has_quota=False with error_message
        if quota is insufficient.
    """
    try:
        # Get user configurations
        user_config = await db_client.get_user_configurations(user.id)

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
                        error_message=(
                            "You have exhausted your trial credits. "
                            "Please email founders@dograh.com for additional Dograh credits "
                            "or change providers in Models configurations."
                        ),
                    )

                logger.info(
                    f"Dograh quota check passed for key ...{api_key[-8:]}: "
                    f"${remaining:.2f} remaining"
                )
            except Exception as e:
                logger.error(f"Failed to check quota for Dograh key: {str(e)}")
                return QuotaCheckResult(
                    has_quota=False,
                    error_message="Could not verify Dograh credits. Please try again.",
                )

        return QuotaCheckResult(has_quota=True)

    except Exception as e:
        logger.error(f"Error during quota check: {str(e)}")
        # On unexpected error, allow the call to proceed
        return QuotaCheckResult(has_quota=True)


async def check_dograh_quota_by_user_id(user_id: int) -> QuotaCheckResult:
    """Check Dograh quota by user ID.

    Convenience function that fetches the user and then checks quota.

    Args:
        user_id: The ID of the user to check quota for

    Returns:
        QuotaCheckResult with quota status
    """
    user = await db_client.get_user_by_id(user_id)
    if not user:
        return QuotaCheckResult(
            has_quota=False,
            error_message="User not found",
        )
    return await check_dograh_quota(user)
