from datetime import datetime, timedelta
from typing import List, Literal, Optional, TypedDict, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError

from api.db import db_client
from api.db.models import (
    UserModel,
)
from api.errors.failure import ErrorSource, classify_exception, log_failure
from api.errors.mps import MPSUnavailableError
from api.schemas.onboarding_state import OnboardingState, OnboardingStateUpdate
from api.schemas.widget_texts import WidgetTexts
from api.schemas.workflow_configurations import (
    CallDispositionOption,
    TextChatInactivityTimeoutConstraints,
    WorkflowConfigurationDefaults,
    get_default_call_disposition_options,
    get_default_workflow_configurations,
)
from api.services.auth.depends import get_user
from api.services.configuration.ai_model_configuration import (
    convert_legacy_ai_model_configuration_to_v2,
    get_resolved_ai_model_configuration,
    update_organization_ai_model_configuration_last_validated_at,
    upsert_organization_ai_model_configuration_v2,
)
from api.services.configuration.check_validity import (
    APIKeyStatusResponse,
    UserConfigurationValidator,
)
from api.services.configuration.defaults import DEFAULT_SERVICE_PROVIDERS
from api.services.configuration.masking import check_for_masked_keys, mask_user_config
from api.services.configuration.merge import merge_user_configurations
from api.services.configuration.registry import REGISTRY, ServiceType
from api.services.mps_service_key_client import mps_service_key_client
from api.services.organization_preferences import (
    get_organization_preferences,
    upsert_organization_preferences,
)
from api.services.user_onboarding import (
    get_onboarding_state,
    update_onboarding_state,
)
from api.services.workflow.answer_classification_service import (
    ANSWER_CLASSIFIER_SYSTEM_PROMPT,
)

router = APIRouter(prefix="/user")


class AuthUserResponse(TypedDict):
    id: int
    is_superuser: bool


class DefaultConfigurationsResponse(BaseModel):
    llm: dict[str, dict]
    tts: dict[str, dict]
    stt: dict[str, dict]
    embeddings: dict[str, dict]
    realtime: dict[str, dict]
    default_providers: dict[str, str]
    workflow_configurations: WorkflowConfigurationDefaults
    default_call_dispositions: list[CallDispositionOption] = Field(
        description=(
            "Built-in suggestions for call-disposition extraction. They do not "
            "enable extraction until saved in workflow_configurations.call_dispositions."
        )
    )
    default_answer_classifier_prompt: str = Field(
        description=(
            "Built-in instructions for the voicemail/screening classifier. The "
            "editor starts from these when a workflow has saved none of its own; "
            "a workflow that has saved instructions keeps showing those."
        )
    )
    text_chat_inactivity_timeout_constraints: TextChatInactivityTimeoutConstraints
    widget_text_defaults: WidgetTexts


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
        "embeddings": {
            provider: model_cls.model_json_schema()
            for provider, model_cls in REGISTRY[ServiceType.EMBEDDINGS].items()
        },
        "realtime": {
            provider: model_cls.model_json_schema()
            for provider, model_cls in REGISTRY[ServiceType.REALTIME].items()
        },
        "default_providers": DEFAULT_SERVICE_PROVIDERS,
        "workflow_configurations": get_default_workflow_configurations(),
        "default_call_dispositions": get_default_call_disposition_options(),
        "default_answer_classifier_prompt": ANSWER_CLASSIFIER_SYSTEM_PROMPT,
        "text_chat_inactivity_timeout_constraints": (
            TextChatInactivityTimeoutConstraints()
        ),
        "widget_text_defaults": WidgetTexts(),
    }
    return DefaultConfigurationsResponse(**configurations)


@router.get("/auth/user")
async def get_auth_user(
    user: UserModel = Depends(get_user),
) -> AuthUserResponse:
    return {
        "id": user.id,
        "is_superuser": user.is_superuser,
    }


class UserConfigurationRequestResponseSchema(BaseModel):
    llm: dict[str, Union[str, float, list[str], None]] | None = None
    tts: dict[str, Union[str, float, list[str], None]] | None = None
    stt: dict[str, Union[str, float, list[str], None]] | None = None
    embeddings: dict[str, Union[str, float, list[str], None]] | None = None
    realtime: dict[str, Union[str, float, list[str], None]] | None = None
    is_realtime: bool | None = None
    test_phone_number: str | None = None
    timezone: str | None = None
    organization_pricing: dict[str, Union[float, str, bool]] | None = None


def _is_validation_cache_stale(
    last_validated_at: datetime | None,
    validity_ttl_seconds: int,
) -> bool:
    if last_validated_at is None:
        return True

    has_timezone = (
        last_validated_at.tzinfo is not None
        and last_validated_at.utcoffset() is not None
    )
    if has_timezone:
        now = datetime.now(last_validated_at.tzinfo)
    else:
        now = datetime.now()
    return last_validated_at < now - timedelta(seconds=validity_ttl_seconds)


@router.get("/configurations/user")
async def get_user_configurations(
    user: UserModel = Depends(get_user),
) -> UserConfigurationRequestResponseSchema:
    resolved_config = await get_resolved_ai_model_configuration(
        organization_id=user.selected_organization_id,
    )
    masked_config = mask_user_config(resolved_config.effective)
    if user.selected_organization_id:
        preferences = await get_organization_preferences(user.selected_organization_id)
        if preferences.test_phone_number is not None:
            masked_config["test_phone_number"] = preferences.test_phone_number
        if preferences.timezone is not None:
            masked_config["timezone"] = preferences.timezone

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
    existing_config = (
        await get_resolved_ai_model_configuration(
            organization_id=user.selected_organization_id,
        )
    ).effective

    incoming_dict = request.model_dump(exclude_none=True)

    # Remove organization_pricing from incoming dict as it's read-only
    incoming_dict.pop("organization_pricing", None)
    preferences_update = {
        key: incoming_dict.pop(key)
        for key in ("test_phone_number", "timezone")
        if key in incoming_dict
    }

    if incoming_dict:
        if not user.selected_organization_id:
            raise HTTPException(status_code=400, detail="No organization selected")

        # Merge via helper
        try:
            user_configurations = merge_user_configurations(
                existing_config, incoming_dict
            )
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))

        try:
            check_for_masked_keys(user_configurations)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        try:
            validator = UserConfigurationValidator()
            await validator.validate(
                user_configurations,
                organization_id=user.selected_organization_id,
                created_by=user.provider_id,
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=e.args[0])

        try:
            organization_configuration = convert_legacy_ai_model_configuration_to_v2(
                user_configurations
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        await upsert_organization_ai_model_configuration_v2(
            user.selected_organization_id,
            organization_configuration,
        )
    else:
        user_configurations = existing_config

    if user.selected_organization_id and preferences_update:
        preferences = await get_organization_preferences(user.selected_organization_id)
        if "test_phone_number" in preferences_update:
            preferences.test_phone_number = preferences_update["test_phone_number"]
        if "timezone" in preferences_update:
            preferences.timezone = preferences_update["timezone"]
        await upsert_organization_preferences(
            user.selected_organization_id,
            preferences,
        )

    # Return masked version of updated config
    masked_config = mask_user_config(user_configurations)
    if user.selected_organization_id:
        preferences = await get_organization_preferences(user.selected_organization_id)
        if preferences.test_phone_number is not None:
            masked_config["test_phone_number"] = preferences.test_phone_number
        if preferences.timezone is not None:
            masked_config["timezone"] = preferences.timezone

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


@router.get("/onboarding-state")
async def get_user_onboarding_state(
    user: UserModel = Depends(get_user),
) -> OnboardingState:
    return await get_onboarding_state(user.id)


@router.put("/onboarding-state")
async def update_user_onboarding_state(
    request: OnboardingStateUpdate,
    user: UserModel = Depends(get_user),
) -> OnboardingState:
    return await update_onboarding_state(user.id, request)


@router.get("/configurations/user/validate")
async def validate_user_configurations(
    validity_ttl_seconds: int = Query(default=60, ge=0, le=86400),
    user: UserModel = Depends(get_user),
) -> APIKeyStatusResponse:
    resolved_config = await get_resolved_ai_model_configuration(
        organization_id=user.selected_organization_id,
    )
    configurations = resolved_config.effective

    if _is_validation_cache_stale(
        configurations.last_validated_at,
        validity_ttl_seconds,
    ):
        validator = UserConfigurationValidator()
        try:
            status = await validator.validate(
                configurations,
                organization_id=user.selected_organization_id,
                created_by=user.provider_id,
            )
            if (
                resolved_config.source == "organization_v2"
                and user.selected_organization_id is not None
            ):
                await update_organization_ai_model_configuration_last_validated_at(
                    user.selected_organization_id
                )
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
TTSProvider = str


class VoiceInfo(BaseModel):
    voice_id: str
    name: str
    description: Optional[str] = None
    accent: Optional[str] = None
    gender: Optional[str] = None
    language: Optional[str] = None
    preview_url: Optional[str] = None


class VoiceFacets(BaseModel):
    """Distinct selector values across a provider's full voice catalog."""

    genders: List[str] = []
    accents: List[str] = []
    languages: List[str] = []


class VoicesResponse(BaseModel):
    provider: str
    voices: List[VoiceInfo]
    facets: Optional[VoiceFacets] = None


PRESET_VOICE_CATALOG: dict[str, list[dict]] = {
    "cartesia": [
        {"voice_id": "f786b574-daa5-4673-aa0c-cbe3e8534c02", "name": "Jasper - Service Specialist", "accent": "gb", "gender": "male", "language": "en", "description": "British · Male · English"},
        {"voice_id": "3faa81ae-d3d8-4ab1-9e44-e50e46d33c30", "name": "Cartesia Default Voice", "accent": "us", "gender": "female", "language": "en", "description": "American · Female · English"},
        {"voice_id": "a0e99841-438c-4a64-b679-ae501e7d6091", "name": "Barbershop Man", "accent": "us", "gender": "male", "language": "en", "description": "American · Male · English"},
        {"voice_id": "79a125e8-cd45-4c13-8a67-188112f4dd22", "name": "British Lady", "accent": "gb", "gender": "female", "language": "en", "description": "British · Female · English"},
        {"voice_id": "69267136-1bdc-4103-a11a-7a0676499302", "name": "Commercial Lady", "accent": "us", "gender": "female", "language": "en", "description": "American · Female · English"},
    ],
    "elevenlabs": [
        {"voice_id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel", "accent": "us", "gender": "female", "language": "en", "description": "Calm & Friendly"},
        {"voice_id": "AZnzlk1XvdvUeBnXmlld", "name": "Domi", "accent": "us", "gender": "female", "language": "en", "description": "Strong & Engaged"},
        {"voice_id": "EXAVITQu4vr4xnSDxMaL", "name": "Bella", "accent": "us", "gender": "female", "language": "en", "description": "Soft & Expressive"},
        {"voice_id": "ErXwobaYiN019PkySvjV", "name": "Antoni", "accent": "us", "gender": "male", "language": "en", "description": "Well-rounded"},
        {"voice_id": "MF3mGyEYCl7XYWbV9V6O", "name": "Elli", "accent": "us", "gender": "female", "language": "en", "description": "Young Emotional"},
        {"voice_id": "TxGEqnHWrfWFTfGW9XjX", "name": "Josh", "accent": "us", "gender": "male", "language": "en", "description": "Deep Authoritative"},
    ],
    "deepgram": [
        {"voice_id": "aura-asteria-en", "name": "Asteria", "accent": "us", "gender": "female", "language": "en", "description": "Confident & Clear"},
        {"voice_id": "aura-luna-en", "name": "Luna", "accent": "us", "gender": "female", "language": "en", "description": "Pleasant & Calm"},
        {"voice_id": "aura-stella-en", "name": "Stella", "accent": "us", "gender": "female", "language": "en", "description": "Warm & Expressive"},
        {"voice_id": "aura-athena-en", "name": "Athena", "accent": "gb", "gender": "female", "language": "en", "description": "Sophisticated British"},
        {"voice_id": "aura-orion-en", "name": "Orion", "accent": "us", "gender": "male", "language": "en", "description": "Authoritative & Deep"},
        {"voice_id": "aura-helios-en", "name": "Helios", "accent": "gb", "gender": "male", "language": "en", "description": "Clear British Male"},
        {"voice_id": "aura-angus-en", "name": "Angus", "accent": "ie", "gender": "male", "language": "en", "description": "Irish Male"},
        {"voice_id": "aura-zeus-en", "name": "Zeus", "accent": "us", "gender": "male", "language": "en", "description": "Deep Resonant Male"},
    ],
    "sarvam": [
        {"voice_id": "anushka", "name": "Anushka", "accent": "in", "gender": "female", "language": "hi", "description": "Natural Indian Female"},
        {"voice_id": "manisha", "name": "Manisha", "accent": "in", "gender": "female", "language": "hi", "description": "Clear Indian Female"},
        {"voice_id": "vidya", "name": "Vidya", "accent": "in", "gender": "female", "language": "hi", "description": "Warm Indian Female"},
        {"voice_id": "arya", "name": "Arya", "accent": "in", "gender": "female", "language": "hi", "description": "Engaging Indian Female"},
        {"voice_id": "abhilash", "name": "Abhilash", "accent": "in", "gender": "male", "language": "hi", "description": "Professional Indian Male"},
        {"voice_id": "karun", "name": "Karun", "accent": "in", "gender": "male", "language": "hi", "description": "Casual Indian Male"},
        {"voice_id": "hitesh", "name": "Hitesh", "accent": "in", "gender": "male", "language": "hi", "description": "Conversational Indian Male"},
    ],
    "openai": [
        {"voice_id": "alloy", "name": "Alloy", "accent": "us", "gender": "neutral", "language": "en", "description": "Balanced & Neutral"},
        {"voice_id": "echo", "name": "Echo", "accent": "us", "gender": "male", "language": "en", "description": "Warm Male"},
        {"voice_id": "fable", "name": "Fable", "accent": "gb", "gender": "male", "language": "en", "description": "British Expressive"},
        {"voice_id": "onyx", "name": "Onyx", "accent": "us", "gender": "male", "language": "en", "description": "Deep Male"},
        {"voice_id": "nova", "name": "Nova", "accent": "us", "gender": "female", "language": "en", "description": "Energetic Female"},
        {"voice_id": "shimmer", "name": "Shimmer", "accent": "us", "gender": "female", "language": "en", "description": "Clear Female"},
    ],
    "google": [
        {"voice_id": "en-US-Chirp3-HD-Charon", "name": "Charon", "accent": "us", "gender": "male", "language": "en", "description": "US English HD Male"},
        {"voice_id": "en-US-Chirp3-HD-Aoede", "name": "Aoede", "accent": "us", "gender": "female", "language": "en", "description": "US English HD Female"},
        {"voice_id": "en-US-Chirp3-HD-Fenrir", "name": "Fenrir", "accent": "us", "gender": "male", "language": "en", "description": "US English HD Male"},
        {"voice_id": "en-US-Chirp3-HD-Kore", "name": "Kore", "accent": "us", "gender": "female", "language": "en", "description": "US English HD Female"},
    ],
    "smallest": [
        {"voice_id": "sophia", "name": "Sophia", "accent": "us", "gender": "female", "language": "en", "description": "American Female"},
        {"voice_id": "emily", "name": "Emily", "accent": "gb", "gender": "female", "language": "en", "description": "British Female"},
        {"voice_id": "aravind", "name": "Aravind", "accent": "in", "gender": "male", "language": "en", "description": "Indian English Male"},
        {"voice_id": "diya", "name": "Diya", "accent": "in", "gender": "female", "language": "hi", "description": "Hindi Indian Female"},
    ],
    "azure": [
        {"voice_id": "en-US-AriaNeural", "name": "Aria", "accent": "us", "gender": "female", "language": "en", "description": "US English Neural Female"},
        {"voice_id": "en-US-GuyNeural", "name": "Guy", "accent": "us", "gender": "male", "language": "en", "description": "US English Neural Male"},
        {"voice_id": "en-US-JennyNeural", "name": "Jenny", "accent": "us", "gender": "female", "language": "en", "description": "US English Neural Female"},
        {"voice_id": "en-IN-NeerjaNeural", "name": "Neerja", "accent": "in", "gender": "female", "language": "en", "description": "Indian English Female"},
        {"voice_id": "en-IN-PrabhatNeural", "name": "Prabhat", "accent": "in", "gender": "male", "language": "en", "description": "Indian English Male"},
    ],
    "rime": [
        {"voice_id": "celeste", "name": "Celeste", "accent": "us", "gender": "female", "language": "en", "description": "Natural Female"},
        {"voice_id": "allison", "name": "Allison", "accent": "us", "gender": "female", "language": "en", "description": "Expressive Female"},
        {"voice_id": "marsh", "name": "Marsh", "accent": "us", "gender": "male", "language": "en", "description": "Smooth Male"},
        {"voice_id": "spire", "name": "Spire", "accent": "us", "gender": "male", "language": "en", "description": "Dynamic Male"},
    ],
    "inworld": [
        {"voice_id": "Ashley", "name": "Ashley", "accent": "us", "gender": "female", "language": "en", "description": "Inworld Character Voice"},
    ],
    "camb": [
        {"voice_id": "147320", "name": "Camb Default", "accent": "us", "gender": "female", "language": "en", "description": "Camb AI Voice"},
    ],
}

MPS_VOICE_PROVIDERS = {"elevenlabs", "deepgram", "sarvam", "cartesia", "dograh", "rime"}


@router.get("/configurations/voices/{provider}")
async def get_voices(
    provider: TTSProvider,
    model: Optional[str] = None,
    language: Optional[str] = None,
    q: Optional[str] = None,
    gender: Optional[str] = None,
    accent: Optional[str] = None,
    user: UserModel = Depends(get_user),
) -> VoicesResponse:
    """Get available voices for a TTS provider."""
    normalized_provider = provider.lower()

    if normalized_provider in MPS_VOICE_PROVIDERS:
        try:
            result = await mps_service_key_client.get_voices(
                provider=normalized_provider,
                model=model,
                language=language,
                q=q,
                gender=gender,
                accent=accent,
                organization_id=user.selected_organization_id,
                created_by=user.provider_id,
            )
            raw_voices = result.get("voices", [])
            if raw_voices:
                return VoicesResponse(
                    provider=result.get("provider", provider),
                    voices=[VoiceInfo(**v) for v in raw_voices],
                    facets=result.get("facets"),
                )
        except Exception:
            pass

    # Fallback to catalog presets
    candidates = PRESET_VOICE_CATALOG.get(normalized_provider, [])
    filtered = []
    q_lower = q.lower().strip() if q else None
    gender_lower = gender.lower().strip() if gender else None
    accent_lower = accent.lower().strip() if accent else None
    language_lower = language.lower().strip() if language else None

    for item in candidates:
        if q_lower and not (
            q_lower in item.get("name", "").lower()
            or q_lower in item.get("voice_id", "").lower()
            or q_lower in item.get("description", "").lower()
        ):
            continue
        if gender_lower and gender_lower != "__all__" and item.get("gender", "").lower() != gender_lower:
            continue
        if accent_lower and accent_lower != "__all__" and item.get("accent", "").lower() != accent_lower:
            continue
        if language_lower and language_lower != "__all__" and item.get("language", "").lower() != language_lower:
            continue
        filtered.append(VoiceInfo(**item))

    # If q was for a specific voice ID that is not in the preset catalog, return a synthetic VoiceInfo
    if q and not filtered:
        filtered.append(VoiceInfo(voice_id=q, name=q, description=f"{provider} voice ID"))

    genders = sorted(list({item["gender"] for item in candidates if item.get("gender")}))
    accents = sorted(list({item["accent"] for item in candidates if item.get("accent")}))
    languages = sorted(list({item["language"] for item in candidates if item.get("language")}))

    return VoicesResponse(
        provider=provider,
        voices=filtered,
        facets=VoiceFacets(genders=genders, accents=accents, languages=languages),
    )
