from datetime import datetime, timedelta
from typing import List, Literal, Optional, TypedDict, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

from api.db import db_client
from api.db.models import (
    UserModel,
)
from api.services.auth.depends import get_user
from api.services.configuration.check_validity import (
    APIKeyStatusResponse,
    UserConfigurationValidator,
)
from api.services.configuration.defaults import DEFAULT_SERVICE_PROVIDERS
from api.services.configuration.masking import mask_user_config
from api.services.configuration.merge import merge_user_configurations
from api.services.configuration.registry import REGISTRY, ServiceType
from api.services.mps_service_key_client import mps_service_key_client

router = APIRouter(prefix="/user")


class AuthUserResponse(TypedDict):
    id: int
    is_superuser: bool


class DefaultConfigurationsResponse(TypedDict):
    llm: dict[str, dict]
    tts: dict[str, dict]
    stt: dict[str, dict]
    default_providers: dict[str, str]


@router.get("/configurations/defaults")
async def get_default_configurations() -> DefaultConfigurationsResponse:
    configurations = {
        "llm": {
            provider: model_cls.model_json_schema()
            for provider, model_cls in REGISTRY[ServiceType.LLM].items()
        },
        "tts": {
            provider: model_cls.model_json_schema()
            for provider, model_cls in REGISTRY[ServiceType.TTS].items()
        },
        "stt": {
            provider: model_cls.model_json_schema()
            for provider, model_cls in REGISTRY[ServiceType.STT].items()
        },
        "default_providers": DEFAULT_SERVICE_PROVIDERS,
    }
    return configurations


@router.get("/auth/user")
async def get_auth_user(
    user: UserModel = Depends(get_user),
) -> AuthUserResponse:
    return {
        "id": user.id,
        "is_superuser": user.is_superuser,
    }


class UserConfigurationRequestResponseSchema(BaseModel):
    llm: dict[str, Union[str, float]] | None = None
    tts: dict[str, Union[str, float]] | None = None
    stt: dict[str, Union[str, float]] | None = None
    test_phone_number: str | None = None
    timezone: str | None = None
    organization_pricing: dict[str, Union[float, str, bool]] | None = None


@router.get("/configurations/user")
async def get_user_configurations(
    user: UserModel = Depends(get_user),
) -> UserConfigurationRequestResponseSchema:
    user_configurations = await db_client.get_user_configurations(user.id)
    masked_config = mask_user_config(user_configurations)

    # Add organization pricing info if available
    if user.selected_organization_id:
        org = await db_client.get_organization_by_id(user.selected_organization_id)
        if org and org.price_per_second_usd is not None:
            masked_config["organization_pricing"] = {
                "price_per_second_usd": org.price_per_second_usd,
                "currency": "USD",
                "billing_enabled": True,
            }

    return masked_config


@router.put("/configurations/user")
async def update_user_configurations(
    request: UserConfigurationRequestResponseSchema,
    user: UserModel = Depends(get_user),
) -> UserConfigurationRequestResponseSchema:
    existing_config = await db_client.get_user_configurations(user.id)

    incoming_dict = request.model_dump(exclude_none=True)

    # Remove organization_pricing from incoming dict as it's read-only
    incoming_dict.pop("organization_pricing", None)

    # Merge via helper
    user_configurations = merge_user_configurations(existing_config, incoming_dict)

    try:
        validator = UserConfigurationValidator()
        await validator.validate(user_configurations)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=e.args[0])

    user_configurations = await db_client.update_user_configuration(
        user.id, user_configurations
    )

    # Return masked version of updated config
    masked_config = mask_user_config(user_configurations)

    # Add organization pricing info if available
    if user.selected_organization_id:
        org = await db_client.get_organization_by_id(user.selected_organization_id)
        if org and org.price_per_second_usd is not None:
            masked_config["organization_pricing"] = {
                "price_per_second_usd": org.price_per_second_usd,
                "currency": "USD",
                "billing_enabled": True,
            }

    return masked_config


@router.get("/configurations/user/validate")
async def validate_user_configurations(
    validity_ttl_seconds: int = Query(default=60, ge=0, le=86400),
    user: UserModel = Depends(get_user),
) -> APIKeyStatusResponse:
    configurations = await db_client.get_user_configurations(user.id)

    if (
        configurations.last_validated_at
        and configurations.last_validated_at
        < datetime.now() - timedelta(seconds=validity_ttl_seconds)
    ):
        validator = UserConfigurationValidator()
        try:
            status = await validator.validate(configurations)
            await db_client.update_user_configuration_last_validated_at(user.id)
            return status
        except ValueError as e:
            raise HTTPException(status_code=422, detail=e.args[0])
    else:
        return {"status": []}


# API Key Management Endpoints
class APIKeyResponse(BaseModel):
    id: int
    name: str
    key_prefix: str
    is_active: bool
    created_at: datetime
    last_used_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None


class CreateAPIKeyRequest(BaseModel):
    name: str


class CreateAPIKeyResponse(BaseModel):
    id: int
    name: str
    key_prefix: str
    api_key: str  # Only returned when creating a new key
    created_at: datetime


@router.get("/api-keys")
async def get_api_keys(
    include_archived: bool = Query(default=False),
    user: UserModel = Depends(get_user),
) -> List[APIKeyResponse]:
    """Get all API keys for the user's selected organization."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    api_keys = await db_client.get_api_keys_by_organization(
        user.selected_organization_id, include_archived=include_archived
    )

    return [
        APIKeyResponse(
            id=key.id,
            name=key.name,
            key_prefix=key.key_prefix,
            is_active=key.is_active,
            created_at=key.created_at,
            last_used_at=key.last_used_at,
            archived_at=key.archived_at,
        )
        for key in api_keys
    ]


@router.post("/api-keys")
async def create_api_key(
    request: CreateAPIKeyRequest,
    user: UserModel = Depends(get_user),
) -> CreateAPIKeyResponse:
    """Create a new API key for the user's selected organization."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    api_key, raw_key = await db_client.create_api_key(
        organization_id=user.selected_organization_id,
        name=request.name,
        created_by=user.id,
    )

    return CreateAPIKeyResponse(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        api_key=raw_key,
        created_at=api_key.created_at,
    )


@router.delete("/api-keys/{api_key_id}")
async def archive_api_key(
    api_key_id: int,
    user: UserModel = Depends(get_user),
) -> dict:
    """Archive an API key (soft delete)."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    # Verify the API key belongs to the user's organization
    api_keys = await db_client.get_api_keys_by_organization(
        user.selected_organization_id, include_archived=True
    )
    if not any(key.id == api_key_id for key in api_keys):
        raise HTTPException(status_code=404, detail="API key not found")

    success = await db_client.archive_api_key(api_key_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to archive API key")

    return {"success": True, "message": "API key archived successfully"}


@router.put("/api-keys/{api_key_id}/reactivate")
async def reactivate_api_key(
    api_key_id: int,
    user: UserModel = Depends(get_user),
) -> dict:
    """Reactivate an archived API key."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    # Verify the API key belongs to the user's organization
    api_keys = await db_client.get_api_keys_by_organization(
        user.selected_organization_id, include_archived=True
    )
    if not any(key.id == api_key_id for key in api_keys):
        raise HTTPException(status_code=404, detail="API key not found")

    success = await db_client.reactivate_api_key(api_key_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to reactivate API key")

    return {"success": True, "message": "API key reactivated successfully"}


# Voice Configuration Endpoints
TTSProvider = Literal["elevenlabs", "deepgram", "sarvam", "cartesia", "dograh"]


class VoiceInfo(BaseModel):
    voice_id: str
    name: str
    description: Optional[str] = None
    accent: Optional[str] = None
    gender: Optional[str] = None
    language: Optional[str] = None
    preview_url: Optional[str] = None


class VoicesResponse(BaseModel):
    provider: str
    voices: List[VoiceInfo]


@router.get("/configurations/voices/{provider}")
async def get_voices(
    provider: TTSProvider,
    user: UserModel = Depends(get_user),
) -> VoicesResponse:
    """Get available voices for a TTS provider."""
    try:
        result = await mps_service_key_client.get_voices(
            provider=provider,
            organization_id=user.selected_organization_id,
            created_by=user.provider_id,
        )
        return VoicesResponse(
            provider=result.get("provider", provider),
            voices=[VoiceInfo(**voice) for voice in result.get("voices", [])],
        )
    except Exception as e:
        logger.error(f"Failed to fetch voices for {provider}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch voices for {provider}",
        )
