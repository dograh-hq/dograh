from typing import Annotated, Optional

import httpx
from fastapi import Header, HTTPException, Query, WebSocket
from loguru import logger
from pydantic import ValidationError

from api.constants import AUTH_PROVIDER, DOGRAH_MPS_SECRET_KEY, MPS_API_URL
from api.db import db_client
from api.db.models import UserModel
from api.schemas.user_configuration import UserConfiguration
from api.services.auth.stack_auth import stackauth
from api.services.configuration.registry import ServiceProviders
from api.utils.auth import decode_jwt_token


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
        user_model = await db_client.get_or_create_user_by_provider_id(stack_user["id"])

        # Sync email from Stack Auth if available and not already set
        stack_email = stack_user.get("primary_email_verified") and stack_user.get(
            "primary_email"
        )
        if stack_email and user_model.email != stack_email:
            await db_client.update_user_email(user_model.id, stack_email)
            user_model.email = stack_email
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

        # Check if user's selected organization differs from the current organization
        if user_model.selected_organization_id != organization.id:
            await db_client.add_user_to_organization(user_model.id, organization.id)

            # Update user's selected organization
            await db_client.update_user_selected_organization(
                user_model.id, organization.id
            )

            # Update the user_model object to reflect the change
            user_model.selected_organization_id = organization.id

            # Only create default configuration if organization was just created
            # This prevents race conditions where multiple concurrent requests
            # might try to create configurations
            if org_was_created:
                existing_cfg = await db_client.get_user_configurations(user_model.id)
                if not (existing_cfg.llm or existing_cfg.tts or existing_cfg.stt):
                    mps_config = await create_user_configuration_with_mps_key(
                        user_model.id, organization.id, stack_user["id"]
                    )
                    if mps_config:
                        await db_client.update_user_configuration(
                            user_model.id, mps_config
                        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to map user to organization: {exc}",
        )

    return user_model


async def get_user_optional(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> UserModel | None:
    """
    Same as get_user but returns None instead of raising 401 if unauthorized.
    Useful for endpoints that need to work both with and without auth.
    """
    try:
        return await get_user(authorization, x_api_key)
    except HTTPException as e:
        if e.status_code == 401:
            return None
        raise


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
) -> Optional[UserConfiguration]:
    """Create user configuration using MPS service key.

    Args:
        user_id: The user's ID
        organization_id: The organization's ID
        user_provider_id: The user's provider ID (for created_by field)

    Returns:
        UserConfiguration with MPS-provided API keys or None if failed
    """

    async with httpx.AsyncClient() as client:
        # Use MPS API URL from constants
        if AUTH_PROVIDER == "local":
            # For local auth mode, create a temporary service key without authentication
            response = await client.post(
                f"{MPS_API_URL}/api/v1/service-keys/",
                json={
                    "name": f"Default Dograh Model Service Key",
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
                    "name": f"Default Dograh Model Service Key",
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
                        "api_key": service_key,
                        "model": "default",
                    },
                    "tts": {
                        "provider": ServiceProviders.DOGRAH.value,
                        "api_key": service_key,
                        "model": "default",
                        "voice": "default",
                    },
                    "stt": {
                        "provider": ServiceProviders.DOGRAH.value,
                        "api_key": service_key,
                        "model": "default",
                    },
                }
                user_config = UserConfiguration(**configuration)
                return user_config
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
