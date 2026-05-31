from typing import Annotated, Optional

import httpx
from fastapi import Header, HTTPException, Query, WebSocket
from loguru import logger

from api.constants import MPS_API_URL
from api.db import db_client
from api.db.models import UserModel
from api.schemas.user_configuration import UserConfiguration
from api.services.configuration.registry import ServiceProviders

# Fixed provider_id for the single default user in no-auth mode
_DEFAULT_USER_PROVIDER_ID = "default-user"


async def _get_or_create_default_user() -> UserModel:
    """Return the singleton application user, creating it with an org on first call."""
    user, was_created = await db_client.get_or_create_user_by_provider_id(
        _DEFAULT_USER_PROVIDER_ID
    )
    if user.selected_organization_id is None:
        org, _ = await db_client.get_or_create_organization_by_provider_id(
            org_provider_id=f"org_{_DEFAULT_USER_PROVIDER_ID}",
            user_id=user.id,
        )
        await db_client.add_user_to_organization(user.id, org.id)
        await db_client.update_user_selected_organization(user.id, org.id)
        user.selected_organization_id = org.id
    return user


async def get_user(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> UserModel:
    """No-auth mode: always return the singleton default user."""
    return await _get_or_create_default_user()


async def get_superuser(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> UserModel:
    """No-auth mode: everyone is an admin — return the default user."""
    return await _get_or_create_default_user()


async def get_user_ws(
    websocket: WebSocket,
    token: str = Query(None),
    api_key: str = Query(None, alias="api_key"),
) -> UserModel:
    """WebSocket auth dependency — no-auth mode, return the default user."""
    return await _get_or_create_default_user()


async def create_user_configuration_with_mps_key(
    user_id: int, organization_id: int, user_provider_id: str
) -> Optional[UserConfiguration]:
    """Create user configuration using MPS service key (OSS mode)."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{MPS_API_URL}/api/v1/service-keys/",
            json={
                "name": "Default Dograh Model Service Key",
                "description": "Auto-generated key for OSS user",
                "expires_in_days": 7,
                "created_by": user_provider_id,
            },
            timeout=10.0,
        )

        if response.status_code == 200:
            data = response.json()
            service_key = data.get("service_key")

            if service_key:
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
                }
                user_config = UserConfiguration(**configuration)
                return user_config
        else:
            logger.warning(
                f"Failed to get MPS service key: {response.status_code} - {response.text}"
            )
