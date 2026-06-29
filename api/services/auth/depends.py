from typing import Annotated, Optional

import httpx
from fastapi import Depends, Header, HTTPException, Query, WebSocket
from loguru import logger
from pydantic import ValidationError

from api.constants import AUTH_PROVIDER, DOGRAH_MPS_SECRET_KEY, MPS_API_URL
from api.db import db_client
from api.db.models import UserModel
from api.enums import PostHogEvent
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.auth.stack_auth import stackauth
from api.services.configuration.registry import ServiceProviders
from api.services.mps_billing import ensure_hosted_mps_billing_account_v2
from api.services.posthog_client import (
    capture_event,
    group_identify,
    set_person_properties,
)
from api.utils.auth import decode_jwt_token

POSTHOG_ORGANIZATION_GROUP_TYPE = "organization"
POSTHOG_ORGANIZATION_USES_MPS_BILLING_V2_PROPERTY = "uses_mps_billing_v2"


async def get_user(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> UserModel:
    # ------------------------------------------------------------------
    # Check if API key is provided (takes precedence)
    # ------------------------------------------------------------------
    if x_api_key:
        return await _handle_api_key_auth(x_api_key)

    # ------------------------------------------------------------------
    # Check if we're using local (email/password) auth
    # ------------------------------------------------------------------
    if AUTH_PROVIDER == "local":
        return await _handle_oss_auth(authorization)

    # ------------------------------------------------------------------
    # 1. Validate and fetch the authenticated Stack user
    # ------------------------------------------------------------------

    stack_user = await stackauth.get_user(authorization)
    if stack_user is None:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # ------------------------------------------------------------------
    # 2. Ensure the user has a team (Stack "selected_team_id")
    # ------------------------------------------------------------------

    selected_team_id: str | None = stack_user.get("selected_team_id")
    if not selected_team_id and stack_user.get("selected_team"):
        selected_team_id = stack_user["selected_team"].get("id")

    if not selected_team_id:
        raise HTTPException(status_code=400, detail="No team selected")

    # ------------------------------------------------------------------
    # 3. Persist/Fetch the local User model
    # ------------------------------------------------------------------

    try:
        (
            user_model,
            user_was_created,
        ) = await db_client.get_or_create_user_by_provider_id(stack_user["id"])

        # Sync email from Stack Auth if available and not already set
        stack_email = stack_user.get("primary_email_verified") and stack_user.get(
            "primary_email"
        )
        if stack_email and user_model.email != stack_email:
            await db_client.update_user_email(user_model.id, stack_email)
            user_model.email = stack_email

        if user_was_created:
            capture_event(
                distinct_id=str(stack_user["id"]),
                event=PostHogEvent.SIGNED_UP,
                properties={
                    "auth_provider": "stack",
                },
            )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error while creating user from database {e}"
        )

    # ------------------------------------------------------------------
    # 4. Persist Organization (team) and mapping in local database
    # ------------------------------------------------------------------

    try:
        (
            organization,
            org_was_created,
        ) = await db_client.get_or_create_organization_by_provider_id(
            org_provider_id=selected_team_id, user_id=user_model.id
        )
        if org_was_created:
            _sync_created_organization_to_posthog(
                organization=organization,
                stack_user=stack_user,
            )

        # Check if user's selected organization differs from the current organization
        if user_model.selected_organization_id != organization.id:
            await db_client.add_user_to_organization(user_model.id, organization.id)

            # Update user's selected organization
            await db_client.update_user_selected_organization(
                user_model.id, organization.id
            )

            # Update the user_model object to reflect the change
            user_model.selected_organization_id = organization.id

            _associate_user_with_posthog_organization(
                user=user_model,
                organization=organization,
                stack_user=stack_user,
                org_was_created=org_was_created,
            )

            # Only create default configuration if organization was just created
            # This prevents race conditions where multiple concurrent requests
            # might try to create configurations
            if org_was_created:
                try:
                    await ensure_hosted_mps_billing_account_v2(
                        organization.id,
                        created_by=str(stack_user["id"]),
                    )
                except Exception:
                    logger.warning(
                        "Failed to initialize hosted MPS billing account for "
                        "organization {}",
                        organization.id,
                        exc_info=True,
                    )

                existing_cfg = await db_client.get_user_configurations(user_model.id)
                if not (existing_cfg.llm or existing_cfg.tts or existing_cfg.stt):
                    mps_config = await create_user_configuration_with_mps_key(
                        user_model.id, organization.id, stack_user["id"]
                    )
                    if mps_config:
                        await db_client.update_user_configuration(
                            user_model.id, mps_config
                        )
                        from api.enums import OrganizationConfigurationKey
                        from api.services.configuration.ai_model_configuration import (
                            convert_legacy_ai_model_configuration_to_v2,
                        )

                        model_config_v2 = convert_legacy_ai_model_configuration_to_v2(
                            mps_config
                        )
                        await db_client.upsert_configuration(
                            organization.id,
                            OrganizationConfigurationKey.MODEL_CONFIGURATION_V2.value,
                            model_config_v2.model_dump(mode="json", exclude_none=True),
                        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to map user to organization: {exc}",
        )

    return user_model


def _sync_created_organization_to_posthog(
    *,
    organization,
    stack_user: dict | None = None,
    created_by_provider_id: str | None = None,
    uses_mps_billing_v2: bool | None = None,
) -> None:
    """Create/update the PostHog organization group for a newly-created org."""
    try:
        organization_id = int(organization.id)
        organization_provider_id = getattr(organization, "provider_id", None)
        created_by = created_by_provider_id
        if created_by is None and stack_user and stack_user.get("id"):
            created_by = str(stack_user["id"])
        properties = {
            "organization_id": organization_id,
            "organization_provider_id": organization_provider_id,
            "auth_provider": "stack",
        }
        if created_by:
            properties["created_by_provider_id"] = created_by
        if uses_mps_billing_v2 is not None:
            properties[POSTHOG_ORGANIZATION_USES_MPS_BILLING_V2_PROPERTY] = (
                uses_mps_billing_v2
            )

        group_identify(
            POSTHOG_ORGANIZATION_GROUP_TYPE,
            str(organization_id),
            properties,
            distinct_id=created_by,
        )
        if created_by:
            capture_event(
                distinct_id=created_by,
                event=PostHogEvent.ORGANIZATION_CREATED,
                properties=properties,
                groups={POSTHOG_ORGANIZATION_GROUP_TYPE: str(organization_id)},
            )
    except Exception:
        logger.exception("Failed to sync created organization to PostHog")


def _sync_posthog_organization_group_properties(
    *,
    organization,
    uses_mps_billing_v2: bool | None = None,
) -> None:
    """Update PostHog organization group properties without creating a person."""
    try:
        organization_id = int(organization.id)
        properties = {
            "organization_id": organization_id,
            "organization_provider_id": getattr(organization, "provider_id", None),
            "auth_provider": "stack",
        }
        if uses_mps_billing_v2 is not None:
            properties[POSTHOG_ORGANIZATION_USES_MPS_BILLING_V2_PROPERTY] = (
                uses_mps_billing_v2
            )

        group_identify(
            POSTHOG_ORGANIZATION_GROUP_TYPE,
            str(organization_id),
            properties,
        )
    except Exception:
        logger.exception("Failed to sync organization group properties to PostHog")


def _sync_posthog_organization_mps_billing_v2_status(
    organization_id: int,
    *,
    uses_mps_billing_v2: bool,
) -> None:
    """Update the PostHog organization group with current MPS billing status."""
    try:
        organization_id = int(organization_id)
        group_identify(
            POSTHOG_ORGANIZATION_GROUP_TYPE,
            str(organization_id),
            {POSTHOG_ORGANIZATION_USES_MPS_BILLING_V2_PROPERTY: uses_mps_billing_v2},
        )
    except Exception:
        logger.exception("Failed to sync organization billing status to PostHog")


def _associate_user_with_posthog_organization(
    *,
    user: UserModel,
    organization,
    stack_user: dict | None = None,
    user_distinct_id: str | None = None,
    org_was_created: bool,
    organization_ids: list[int] | None = None,
    selected_organization_id: int | None = None,
    selected_organization_provider_id: str | None = None,
) -> None:
    """Attach the Stack user to the PostHog organization group."""
    try:
        organization_id = int(organization.id)
        organization_provider_id = getattr(organization, "provider_id", None)
        if user_distinct_id is None:
            if stack_user and stack_user.get("id"):
                user_distinct_id = str(stack_user["id"])
            else:
                user_distinct_id = str(user.provider_id)
        selected_org_id = selected_organization_id or organization_id
        selected_org_provider_id = (
            selected_organization_provider_id or organization_provider_id
        )
        person_properties = {
            "user_id": user.id,
            "user_provider_id": user_distinct_id,
            "selected_organization_id": selected_org_id,
            "selected_organization_provider_id": selected_org_provider_id,
        }
        if organization_ids is not None:
            person_properties["organization_ids"] = organization_ids
        if user.email:
            person_properties["email"] = user.email
        set_person_properties(user_distinct_id, person_properties)
        event_properties = {
            "user_id": user.id,
            "organization_id": organization_id,
            "organization_provider_id": organization_provider_id,
            "auth_provider": "stack",
            "organization_was_created": org_was_created,
        }

        capture_event(
            distinct_id=user_distinct_id,
            event=PostHogEvent.ORGANIZATION_USER_ASSOCIATED,
            properties=event_properties,
            groups={POSTHOG_ORGANIZATION_GROUP_TYPE: str(organization_id)},
        )
    except Exception:
        logger.exception("Failed to associate user with PostHog organization")


async def get_user_with_selected_organization(
    user: Annotated[UserModel, Depends(get_user)],
) -> UserModel:
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return user


async def _handle_oss_auth(authorization: str | None) -> UserModel:
    """
    Handle authentication for OSS deployment mode.
    Validates JWT tokens issued by the email/password auth flow.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")

    # Remove "Bearer " prefix if present
    token = (
        authorization.replace("Bearer ", "")
        if authorization.startswith("Bearer ")
        else authorization
    )

    if not token:
        raise HTTPException(status_code=401, detail="Invalid authorization token")

    try:
        payload = decode_jwt_token(token)
        user = await db_client.get_user_by_id(int(payload["sub"]))
        if user:
            return user
        raise HTTPException(status_code=401, detail="User not found")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def _handle_api_key_auth(api_key: str) -> UserModel:
    """
    Handle authentication via X-API-Key header.
    Returns the user who created the API key with the correct organization context.
    """
    # Validate the API key
    api_key_model = await db_client.validate_api_key(api_key)

    if not api_key_model:
        raise HTTPException(status_code=401, detail="Invalid or expired API key")

    # API key must have a created_by user
    if not api_key_model.created_by:
        raise HTTPException(status_code=401, detail="API key has no associated user")

    # Get the user who created this API key
    user = await db_client.get_user_by_id(api_key_model.created_by)
    if not user:
        raise HTTPException(status_code=401, detail="API key owner not found")

    # Set the organization context to the API key's organization
    user.selected_organization_id = api_key_model.organization_id

    logger.debug(
        f"Authenticated via API key: {api_key_model.key_prefix}... "
        f"(user_id={user.id}, org_id={api_key_model.organization_id})"
    )

    return user


async def create_user_configuration_with_mps_key(
    user_id: int, organization_id: int, user_provider_id: str
) -> Optional[EffectiveAIModelConfiguration]:
    """Create user configuration using MPS service key.

    Args:
        user_id: The user's ID
        organization_id: The organization's ID
        user_provider_id: The user's provider ID (for created_by field)

    Returns:
        EffectiveAIModelConfiguration with MPS-provided API keys or None if failed
    """

    async with httpx.AsyncClient() as client:
        # Use MPS API URL from constants
        if AUTH_PROVIDER == "local":
            # For local auth mode, create a temporary service key without authentication
            response = await client.post(
                f"{MPS_API_URL}/api/v1/service-keys/",
                json={
                    "name": "Default Dograh Model Service Key",
                    "description": "Auto-generated key for OSS user",
                    "expires_in_days": 7,  # Short-lived for OSS
                    "created_by": user_provider_id,
                },
                timeout=10.0,
            )
        else:
            # For authenticated mode, use the secret key and organization ID
            if not DOGRAH_MPS_SECRET_KEY:
                logger.warning(
                    "Warning: DOGRAH_MPS_SECRET_KEY not set for authenticated mode"
                )
                raise ValidationError("Missing DOGRAH_MPS_SECRET_KEY in non oss mode")

            response = await client.post(
                f"{MPS_API_URL}/api/v1/service-keys/",
                json={
                    "name": "Default Dograh Model Service Key",
                    "description": f"Auto-generated key for organization {organization_id}",
                    "organization_id": organization_id,
                    "expires_in_days": 90,  # Longer-lived for authenticated users
                    "created_by": user_provider_id,
                },
                headers={"X-Secret-Key": DOGRAH_MPS_SECRET_KEY},
                timeout=10.0,
            )

        if response.status_code == 200:
            data = response.json()
            service_key = data.get("service_key")

            if service_key:
                # Create configuration JSON for storage in database
                # The service_factory will use this to instantiate actual services
                configuration = {
                    "llm": {
                        "provider": ServiceProviders.DOGRAH.value,
                        "api_key": [service_key],
                        "model": "default",
                    },
                    "tts": {
                        "provider": ServiceProviders.DOGRAH.value,
                        "api_key": [service_key],
                        "model": "default",
                        "voice": "default",
                    },
                    "stt": {
                        "provider": ServiceProviders.DOGRAH.value,
                        "api_key": [service_key],
                        "model": "default",
                    },
                    "embeddings": {
                        "provider": ServiceProviders.DOGRAH.value,
                        "api_key": [service_key],
                        "model": "dograh_embedding_v1",
                    },
                }
                effective_config = EffectiveAIModelConfiguration(**configuration)
                return effective_config
        else:
            logger.warning(
                f"Failed to get MPS service key: {response.status_code} - {response.text}"
            )


async def get_superuser(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> UserModel:
    """
    Dependency to check if the authenticated user is a superuser.
    Raises HTTPException if user is not authenticated or not a superuser.
    """
    user = await get_user(authorization, x_api_key)

    if not user.is_superuser:
        raise HTTPException(
            status_code=403, detail="Access denied. Superuser privileges required."
        )

    return user


async def get_user_ws(
    websocket: WebSocket,
    token: str = Query(None),
    api_key: str = Query(None, alias="api_key"),
) -> UserModel:
    """
    WebSocket authentication dependency.
    Uses token or api_key from query parameters for authentication.
    """
    if not token and not api_key:
        await websocket.close(code=1008, reason="Missing authentication token")
        raise HTTPException(status_code=401, detail="Missing authentication token")

    try:
        # API key takes precedence
        if api_key:
            user = await get_user(None, api_key)
        else:
            # Use the same logic as get_user but with token from query
            authorization = f"Bearer {token}"
            user = await get_user(authorization, None)
        return user
    except HTTPException as e:
        await websocket.close(code=1008, reason=e.detail)
        raise
