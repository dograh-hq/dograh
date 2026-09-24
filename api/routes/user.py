from datetime import datetime, timedelta
import hashlib
import json
from typing import Annotated, List, Literal, Optional, TypedDict, Union

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field, ValidationError
import redis.asyncio as aioredis

from api.constants import REDIS_URL

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
from api.services.auth.depends import get_user, get_user_with_selected_organization
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


class SupportedLanguage(BaseModel):
    code: str
    name: str
    flag: Optional[str] = "🌐"


class VoiceFacets(BaseModel):
    """Distinct selector values across a provider's full voice catalog."""

    genders: List[str] = []
    accents: List[str] = []
    languages: List[str] = []


class VoicesResponse(BaseModel):
    provider: str
    voices: List[VoiceInfo]
    facets: Optional[VoiceFacets] = None
    supported_languages: List[SupportedLanguage] = []


PROVIDER_SUPPORTED_LANGUAGES: dict[str, list[dict]] = {
    "sarvam": [
        {"code": "hi-IN", "name": "Hindi", "flag": "🇮🇳"},
        {"code": "en-IN", "name": "Indian English", "flag": "🇮🇳"},
        {"code": "bn-IN", "name": "Bengali", "flag": "🇮🇳"},
        {"code": "ta-IN", "name": "Tamil", "flag": "🇮🇳"},
        {"code": "te-IN", "name": "Telugu", "flag": "🇮🇳"},
        {"code": "mr-IN", "name": "Marathi", "flag": "🇮🇳"},
        {"code": "gu-IN", "name": "Gujarati", "flag": "🇮🇳"},
        {"code": "kn-IN", "name": "Kannada", "flag": "🇮🇳"},
        {"code": "pa-IN", "name": "Punjabi", "flag": "🇮🇳"},
        {"code": "ml-IN", "name": "Malayalam", "flag": "🇮🇳"},
        {"code": "od-IN", "name": "Odia", "flag": "🇮🇳"},
    ],
    "cartesia": [
        {"code": "en", "name": "English", "flag": "🇺🇸"},
        {"code": "hi", "name": "Hindi", "flag": "🇮🇳"},
        {"code": "es", "name": "Spanish", "flag": "🇪🇸"},
        {"code": "fr", "name": "French", "flag": "🇫🇷"},
        {"code": "de", "name": "German", "flag": "🇩🇪"},
        {"code": "ja", "name": "Japanese", "flag": "🇯🇵"},
        {"code": "pt", "name": "Portuguese", "flag": "🇧🇷"},
        {"code": "zh", "name": "Chinese", "flag": "🇨🇳"},
    ],
    "elevenlabs": [
        {"code": "en", "name": "English", "flag": "🇺🇸"},
        {"code": "hi", "name": "Hindi", "flag": "🇮🇳"},
        {"code": "es", "name": "Spanish", "flag": "🇪🇸"},
        {"code": "fr", "name": "French", "flag": "🇫🇷"},
        {"code": "de", "name": "German", "flag": "🇩🇪"},
        {"code": "it", "name": "Italian", "flag": "🇮🇹"},
        {"code": "pt", "name": "Portuguese", "flag": "🇧🇷"},
        {"code": "pl", "name": "Polish", "flag": "🇵🇱"},
        {"code": "ja", "name": "Japanese", "flag": "🇯🇵"},
        {"code": "ar", "name": "Arabic", "flag": "🇦🇪"},
    ],
    "deepgram": [
        {"code": "en", "name": "English (US)", "flag": "🇺🇸"},
        {"code": "en-GB", "name": "English (UK)", "flag": "🇬🇧"},
        {"code": "en-IE", "name": "English (Ireland)", "flag": "🇮🇪"},
    ],
    "openai": [
        {"code": "en", "name": "English", "flag": "🇺🇸"},
        {"code": "hi", "name": "Hindi", "flag": "🇮🇳"},
        {"code": "es", "name": "Spanish", "flag": "🇪🇸"},
        {"code": "fr", "name": "French", "flag": "🇫🇷"},
        {"code": "de", "name": "German", "flag": "🇩🇪"},
        {"code": "ja", "name": "Japanese", "flag": "🇯🇵"},
    ],
    "smallest": [
        {"code": "en", "name": "English", "flag": "🇺🇸"},
        {"code": "hi", "name": "Hindi", "flag": "🇮🇳"},
    ],
    "azure": [
        {"code": "en-US", "name": "US English", "flag": "🇺🇸"},
        {"code": "en-IN", "name": "Indian English", "flag": "🇮🇳"},
        {"code": "hi-IN", "name": "Hindi", "flag": "🇮🇳"},
    ],
    "speechify": [
        {"code": "hi", "name": "Hindi", "flag": "🇮🇳"},
        {"code": "en-IN", "name": "Indian English", "flag": "🇮🇳"},
        {"code": "en", "name": "English (US)", "flag": "🇺🇸"},
        {"code": "en-GB", "name": "English (UK)", "flag": "🇬🇧"},
        {"code": "es", "name": "Spanish (Spain)", "flag": "🇪🇸"},
        {"code": "es-MX", "name": "Spanish (Mexico)", "flag": "🇲🇽"},
        {"code": "de", "name": "German", "flag": "🇩🇪"},
        {"code": "fr", "name": "French", "flag": "🇫🇷"},
        {"code": "it", "name": "Italian", "flag": "🇮🇹"},
        {"code": "pt-BR", "name": "Portuguese (Brazil)", "flag": "🇧🇷"},
    ],
}


VOICE_CACHE_TTL = 86400  # 24 hours

_VOICE_REDIS_CLIENT: Optional[aioredis.Redis] = None


async def _get_voice_redis() -> Optional[aioredis.Redis]:
    """Get or initialize singleton Redis client for voice caching."""
    global _VOICE_REDIS_CLIENT
    if _VOICE_REDIS_CLIENT is None:
        try:
            _VOICE_REDIS_CLIENT = await aioredis.from_url(REDIS_URL, decode_responses=True)
        except Exception as e:
            logger.warning("Could not connect to Redis for voice caching: {}", e)
            return None
    return _VOICE_REDIS_CLIENT


async def _get_optional_user(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Optional[UserModel]:
    """Graceful user resolution for public voice catalogs."""
    if not authorization and not x_api_key:
        return None
    try:
        return await get_user(authorization, x_api_key)
    except Exception:
        return None


@router.get("/configurations/voices/default")
async def get_default_voices(
    model: Optional[str] = None,
    language: Optional[str] = None,
    q: Optional[str] = None,
    gender: Optional[str] = None,
    accent: Optional[str] = None,
    refresh: bool = False,
    user: Optional[UserModel] = Depends(_get_optional_user),
) -> VoicesResponse:
    """
    Get available voices for the admin's configured default TTS provider from Dograh MPS.
    Automatically resolves the default master provider and model set by the platform admin,
    and caches responses in Redis to avoid repetitive MPS requests.
    """
    admin_provider = "cartesia"
    admin_model = model
    try:
        from api.services.platform_keys import get_default_platform_provider_and_model
        default_result = get_default_platform_provider_and_model("tts")
        if default_result and default_result[0]:
            admin_provider = default_result[0].lower()
            if not admin_model and default_result[2]:
                admin_model = default_result[2]
    except Exception as e:
        logger.debug("Could not resolve default platform TTS provider: {}", e)
        admin_provider = DEFAULT_SERVICE_PROVIDERS.get("tts", "cartesia").lower()

    return await get_voices(
        provider=admin_provider,
        model=admin_model,
        language=language,
        q=q,
        gender=gender,
        accent=accent,
        refresh=refresh,
        user=user,
    )


@router.get("/configurations/voices/{provider}")
async def get_voices(
    provider: TTSProvider,
    model: Optional[str] = None,
    language: Optional[str] = None,
    q: Optional[str] = None,
    gender: Optional[str] = None,
    accent: Optional[str] = None,
    refresh: bool = False,
    user: Optional[UserModel] = Depends(_get_optional_user),
) -> VoicesResponse:
    """
    Get available voices for a TTS provider dynamically from Dograh MPS (services.dograh.com)
    with Redis caching.
    """
    normalized_provider = str(provider).lower()

    # Build unique Redis cache key
    cache_key_elements = f"{normalized_provider}:{model or ''}:{language or ''}:{q or ''}:{gender or ''}:{accent or ''}"
    cache_key_hash = hashlib.md5(cache_key_elements.encode("utf-8")).hexdigest()
    redis_cache_key = f"voices:cache:{normalized_provider}:{cache_key_hash}"

    redis_client = await _get_voice_redis()
    if redis_client and not refresh:
        try:
            cached_data = await redis_client.get(redis_cache_key)
            if cached_data:
                cached_dict = json.loads(cached_data)
                return VoicesResponse(**cached_dict)
        except Exception as e:
            logger.warning("Redis voice cache read error: {}", e)

    # Fetch dynamically from MPS voice proxy URL (https://services.dograh.com/api/v1/voice-proxy/{provider}/voices)
    result = await mps_service_key_client.get_voices(
        provider=normalized_provider,
        model=model,
        language=language,
        q=q,
        gender=gender,
        accent=accent,
        organization_id=user.selected_organization_id if user else None,
        created_by=user.provider_id if user else None,
    )

    raw_voices = []
    facets_data = None
    resolved_provider = normalized_provider

    if isinstance(result, dict):
        resolved_provider = result.get("provider", normalized_provider)
        raw_voices = result.get("voices", [])
        facets_data = result.get("facets")
    elif isinstance(result, list):
        raw_voices = result

    # Normalize voice items dynamically
    parsed_voices: list[VoiceInfo] = []
    for item in raw_voices:
        if not isinstance(item, dict):
            continue
        v_id = str(item.get("voice_id") or item.get("id") or "").strip()
        if not v_id:
            continue
        v_name = str(item.get("name") or v_id)
        v_accent = item.get("accent")
        loc = item.get("locale") or item.get("language")
        if not v_accent and loc and "-" in str(loc):
            v_accent = str(loc).split("-")[-1].lower()

        parsed_voices.append(
            VoiceInfo(
                voice_id=v_id,
                name=v_name,
                accent=v_accent,
                gender=(item.get("gender") or "neutral").lower() if item.get("gender") else None,
                language=str(item.get("language") or (loc.split("-")[0] if loc else "en")).lower(),
                description=item.get("description") or f"{resolved_provider.title()} Voice",
                preview_url=item.get("preview_url") or item.get("preview_audio") or item.get("sample_audio_url"),
            )
        )

    # Multi-criteria filtering if requested
    filtered: list[VoiceInfo] = []
    q_lower = q.lower().strip() if q else None
    gender_lower = gender.lower().strip() if gender else None
    accent_lower = accent.lower().strip() if accent else None
    language_lower = language.lower().strip() if language else None

    for voice in parsed_voices:
        if q_lower and not (
            q_lower in voice.name.lower()
            or q_lower in voice.voice_id.lower()
            or (voice.description and q_lower in voice.description.lower())
        ):
            continue
        if gender_lower and gender_lower != "__all__" and voice.gender and voice.gender.lower() != gender_lower:
            continue
        if accent_lower and accent_lower != "__all__" and voice.accent and voice.accent.lower() != accent_lower:
            continue
        if language_lower and language_lower != "__all__" and voice.language and voice.language.lower() != language_lower:
            continue
        filtered.append(voice)

    # Derive facets dynamically from live voices
    if isinstance(facets_data, dict):
        facets = VoiceFacets(**facets_data)
    elif parsed_voices:
        genders = sorted(list({v.gender for v in parsed_voices if v.gender}))
        accents = sorted(list({v.accent for v in parsed_voices if v.accent}))
        languages = sorted(list({v.language for v in parsed_voices if v.language}))
        facets = VoiceFacets(genders=genders, accents=accents, languages=languages)
    else:
        facets = VoiceFacets()

    # Supported language indicators
    raw_supp = PROVIDER_SUPPORTED_LANGUAGES.get(
        normalized_provider, [{"code": "en", "name": "English", "flag": "🌐"}]
    )
    supported_langs = [SupportedLanguage(**l) for l in raw_supp]

    response = VoicesResponse(
        provider=resolved_provider,
        voices=filtered,
        facets=facets,
        supported_languages=supported_langs,
    )

    # Cache into Redis
    if redis_client and response.voices:
        try:
            await redis_client.set(
                redis_cache_key,
                response.model_dump_json(),
                ex=VOICE_CACHE_TTL,
            )
        except Exception as e:
            logger.warning("Redis voice cache write error: {}", e)

    return response


# ---------------------------------------------------------------------------
# Organization Settings & Profile Endpoints
# ---------------------------------------------------------------------------

class OrganizationCallingPreferences(BaseModel):
    calling_hours: str = "10:00 – 19:00"
    language: str = "English + Hindi"
    voice: str = "Ananya (female)"
    record_calls: bool = True


class OrganizationNotifications(BaseModel):
    campaign_completed: bool = True
    low_balance: bool = True
    payment_receipts: bool = True
    call_failures: bool = False


class OrganizationSettingsResponse(BaseModel):
    id: int
    name: str
    industry: Optional[str] = "Real estate"
    website: Optional[str] = ""
    phone: Optional[str] = ""
    address: Optional[str] = ""
    gstin: Optional[str] = ""
    calling_preferences: OrganizationCallingPreferences
    notifications: OrganizationNotifications
    email: Optional[str] = ""
    subscription_tier: Optional[str] = "simple_trial"
    created_at: Optional[str] = None


class OrganizationSettingsUpdateRequest(BaseModel):
    name: Optional[str] = None
    industry: Optional[str] = None
    website: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    gstin: Optional[str] = None
    calling_preferences: Optional[OrganizationCallingPreferences] = None
    notifications: Optional[OrganizationNotifications] = None


@router.get("/organization", response_model=OrganizationSettingsResponse)
async def get_user_organization_settings(
    user: UserModel = Depends(get_user_with_selected_organization),
):
    """Retrieve settings and profile for the user's selected organization."""
    org_id = user.selected_organization_id
    org = await db_client.get_organization_by_id(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    biz_config = await db_client.get_configuration(org_id, "BUSINESS_PROFILE")
    biz_data = biz_config.value if biz_config and isinstance(biz_config.value, dict) else {}

    call_config = await db_client.get_configuration(org_id, "CALLING_PREFERENCES")
    call_data = call_config.value if call_config and isinstance(call_config.value, dict) else {}

    notif_config = await db_client.get_configuration(org_id, "NOTIFICATION_SETTINGS")
    notif_data = notif_config.value if notif_config and isinstance(notif_config.value, dict) else {}

    return OrganizationSettingsResponse(
        id=org.id,
        name=biz_data.get("name") or org.provider_id or "My Workspace",
        industry=biz_data.get("industry", "Real estate"),
        website=biz_data.get("website", ""),
        phone=biz_data.get("phone", ""),
        address=biz_data.get("address", ""),
        gstin=biz_data.get("gstin", ""),
        calling_preferences=OrganizationCallingPreferences(
            calling_hours=call_data.get("calling_hours", "10:00 – 19:00"),
            language=call_data.get("language", "English + Hindi"),
            voice=call_data.get("voice", "Ananya (female)"),
            record_calls=call_data.get("record_calls", True),
        ),
        notifications=OrganizationNotifications(
            campaign_completed=notif_data.get("campaign_completed", True),
            low_balance=notif_data.get("low_balance", True),
            payment_receipts=notif_data.get("payment_receipts", True),
            call_failures=notif_data.get("call_failures", False),
        ),
        email=user.email or "",
        subscription_tier=getattr(org, "subscription_tier", "simple_trial") or "simple_trial",
        created_at=org.created_at.isoformat() if org.created_at else None,
    )


@router.patch("/organization", response_model=OrganizationSettingsResponse)
async def update_user_organization_settings(
    request: OrganizationSettingsUpdateRequest,
    user: UserModel = Depends(get_user_with_selected_organization),
):
    """Update settings and business profile for the user's selected organization."""
    from api.db.models import OrganizationModel
    from sqlalchemy import update

    org_id = user.selected_organization_id
    org = await db_client.get_organization_by_id(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # 1. Update company / provider_id if provided
    if request.name and request.name.strip():
        new_name = request.name.strip()
        async with db_client.async_session() as session:
            stmt = update(OrganizationModel).where(OrganizationModel.id == org_id).values(provider_id=new_name)
            await session.execute(stmt)
            await session.commit()
        org.provider_id = new_name

    # 2. Update BUSINESS_PROFILE
    biz_config = await db_client.get_configuration(org_id, "BUSINESS_PROFILE")
    biz_data = biz_config.value if biz_config and isinstance(biz_config.value, dict) else {}

    if request.name is not None:
        biz_data["name"] = request.name
    if request.industry is not None:
        biz_data["industry"] = request.industry
    if request.website is not None:
        biz_data["website"] = request.website
    if request.phone is not None:
        biz_data["phone"] = request.phone
    if request.address is not None:
        biz_data["address"] = request.address
    if request.gstin is not None:
        biz_data["gstin"] = request.gstin.upper()

    await db_client.upsert_configuration(org_id, "BUSINESS_PROFILE", biz_data)

    # 3. Update CALLING_PREFERENCES if provided
    call_config = await db_client.get_configuration(org_id, "CALLING_PREFERENCES")
    call_data = call_config.value if call_config and isinstance(call_config.value, dict) else {}
    if request.calling_preferences is not None:
        call_data = request.calling_preferences.model_dump()
        await db_client.upsert_configuration(org_id, "CALLING_PREFERENCES", call_data)

    # 4. Update NOTIFICATION_SETTINGS if provided
    notif_config = await db_client.get_configuration(org_id, "NOTIFICATION_SETTINGS")
    notif_data = notif_config.value if notif_config and isinstance(notif_config.value, dict) else {}
    if request.notifications is not None:
        notif_data = request.notifications.model_dump()
        await db_client.upsert_configuration(org_id, "NOTIFICATION_SETTINGS", notif_data)

    return OrganizationSettingsResponse(
        id=org.id,
        name=biz_data.get("name") or org.provider_id or "My Workspace",
        industry=biz_data.get("industry", "Real estate"),
        website=biz_data.get("website", ""),
        phone=biz_data.get("phone", ""),
        address=biz_data.get("address", ""),
        gstin=biz_data.get("gstin", ""),
        calling_preferences=OrganizationCallingPreferences(
            calling_hours=call_data.get("calling_hours", "10:00 – 19:00"),
            language=call_data.get("language", "English + Hindi"),
            voice=call_data.get("voice", "Ananya (female)"),
            record_calls=call_data.get("record_calls", True),
        ),
        notifications=OrganizationNotifications(
            campaign_completed=notif_data.get("campaign_completed", True),
            low_balance=notif_data.get("low_balance", True),
            payment_receipts=notif_data.get("payment_receipts", True),
            call_failures=notif_data.get("call_failures", False),
        ),
        email=user.email or "",
        subscription_tier=getattr(org, "subscription_tier", "simple_trial") or "simple_trial",
        created_at=org.created_at.isoformat() if org.created_at else None,
    )

