import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_superuser
from api.services.auth.stack_auth import (
    StackAuthSessionError,
    StackAuthUserSearchError,
    stackauth,
)
from api.services.platform_keys import refresh_master_keys_cache

router = APIRouter(prefix="/superuser", tags=["superuser"])


class ImpersonateRequest(BaseModel):
    """Request payload for superadmin impersonation.

    ``provider_user_id``, ``user_id``, or ``email`` may be supplied. If more
    than one is provided, ``provider_user_id`` takes precedence, followed by
    ``user_id`` and then ``email``.
    """

    provider_user_id: str | None = None
    user_id: int | None = None
    email: str | None = None


class ImpersonateResponse(BaseModel):
    refresh_token: str
    access_token: str


class SuperuserWorkflowRunResponse(BaseModel):
    id: int
    name: str
    workflow_id: int
    workflow_name: Optional[str]
    user_id: Optional[int]
    organization_id: Optional[int]
    organization_name: Optional[str]
    mode: str
    is_completed: bool
    recording_url: Optional[str]
    transcript_url: Optional[str]
    usage_info: Optional[dict]
    cost_info: Optional[dict]
    initial_context: Optional[dict]
    gathered_context: Optional[dict]
    created_at: datetime


class SuperuserWorkflowRunsListResponse(BaseModel):
    workflow_runs: List[SuperuserWorkflowRunResponse]
    total_count: int
    page: int
    limit: int
    total_pages: int


@router.post("/impersonate")
async def impersonate(
    request: ImpersonateRequest, user: UserModel = Depends(get_superuser)
) -> ImpersonateResponse:
    """Impersonate a user as a super-admin.
    Internally, Stack Auth requires the **provider user ID** (a UUID-ish string)
    to create an impersonation session.
    """

    provider_user_id = (
        request.provider_user_id.strip() if request.provider_user_id else None
    ) or None
    email = request.email.strip().lower() if request.email else None

    # ------------------------------------------------------------------
    # Fallback: resolve provider_user_id from internal ``user_id`` or email.
    # ------------------------------------------------------------------
    if provider_user_id is None:
        if request.user_id is not None:
            db_user = await db_client.get_user_by_id(request.user_id)

            if db_user is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"User with ID {request.user_id} not found.",
                )

            provider_user_id = db_user.provider_id
        elif email:
            db_user = await db_client.get_user_by_email(email)

            if db_user is not None:
                provider_user_id = db_user.provider_id
            else:
                try:
                    stack_users = await stackauth.find_users_by_email(email)
                except StackAuthUserSearchError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="Failed to search Stack Auth users.",
                    ) from exc

                if len(stack_users) == 1 and isinstance(stack_users[0].get("id"), str):
                    provider_user_id = stack_users[0]["id"]
                elif len(stack_users) > 1:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Multiple Stack Auth users matched that email.",
                    )
                else:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"User with email {email} not found.",
                    )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "One of 'provider_user_id', 'user_id', or 'email' must be provided."
                ),
            )

    # ------------------------------------------------------------------
    # Call Stack Auth to create the impersonation session
    # ------------------------------------------------------------------
    try:
        session = await stackauth.impersonate(provider_user_id)
    except StackAuthSessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create Stack Auth impersonation session.",
        ) from exc

    if (
        not isinstance(session, dict)
        or "refresh_token" not in session
        or "access_token" not in session
    ):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create Stack Auth impersonation session.",
        )

    return ImpersonateResponse(
        refresh_token=session["refresh_token"],
        access_token=session["access_token"],
    )


@router.get("/workflow-runs")
async def get_workflow_runs(
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    limit: int = Query(50, ge=1, le=100, description="Number of items per page"),
    filters: Optional[str] = Query(None, description="JSON-encoded filter criteria"),
    sort_by: Optional[str] = Query(
        None, description="Field to sort by (e.g., 'duration', 'created_at')"
    ),
    sort_order: Optional[str] = Query(
        "desc", description="Sort order ('asc' or 'desc')"
    ),
    user: UserModel = Depends(get_superuser),
) -> SuperuserWorkflowRunsListResponse:
    """
    Get paginated list of all workflow runs with organization information.
    Requires superuser privileges.

    Filters should be provided as a JSON-encoded array of filter criteria.
    Example: [{"field": "id", "type": "number", "value": {"value": 680}}]
    """
    offset = (page - 1) * limit

    # Parse filters if provided
    filter_criteria = None
    if filters:
        try:
            filter_criteria = json.loads(filters)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid filter format")

    # Validate sort_order
    if sort_order not in ("asc", "desc"):
        sort_order = "desc"

    workflow_runs, total_count = await db_client.get_workflow_runs_for_superadmin(
        limit=limit,
        offset=offset,
        filters=filter_criteria,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    total_pages = (total_count + limit - 1) // limit  # Ceiling division

    return SuperuserWorkflowRunsListResponse(
        workflow_runs=[SuperuserWorkflowRunResponse(**run) for run in workflow_runs],
        total_count=total_count,
        page=page,
        limit=limit,
        total_pages=total_pages,
    )


# =========================================================================
# Platform Master Keys Management (LLM, STT, TTS)
# =========================================================================

class MasterKeyCreateRequest(BaseModel):
    service_type: str  # 'llm', 'stt', 'tts'
    provider: str      # 'openai', 'groq', 'deepgram', 'cartesia', 'elevenlabs', etc.
    api_key: str
    is_default: bool = False
    default_model: Optional[str] = None
    default_voice: Optional[str] = None
    is_active: bool = True
    models_pricing: Optional[dict] = None


class MasterKeyUpdateRequest(BaseModel):
    api_key: Optional[str] = None
    is_default: Optional[bool] = None
    default_model: Optional[str] = None
    default_voice: Optional[str] = None
    is_active: Optional[bool] = None
    models_pricing: Optional[dict] = None


class MasterKeySetDefaultModelRequest(BaseModel):
    default_model: str


class MasterKeySetDefaultVoiceRequest(BaseModel):
    default_voice: str


class MasterKeyResponse(BaseModel):
    id: int
    service_type: str
    provider: str
    key_prefix: str
    is_default: bool
    default_model: Optional[str] = None
    default_voice: Optional[str] = None
    is_active: bool
    models_pricing: dict
    created_at: datetime
    updated_at: datetime


@router.get("/platform/providers-registry")
async def get_platform_providers_registry(
    current_user: UserModel = Depends(get_superuser),
):
    """Return all platform engine providers and their model lists directly from REGISTRY."""
    from api.services.configuration.registry import REGISTRY, ServiceType

    def extract_provider_info(service_type: ServiceType):
        result = []
        registry_map = REGISTRY.get(service_type, {})
        for provider, model_cls in registry_map.items():
            prov_key = provider.value if hasattr(provider, "value") else str(provider)
            if prov_key == "dograh":
                continue
            schema = model_cls.model_json_schema()
            model_prop = schema.get("properties", {}).get("model", {})
            models = []
            if "examples" in model_prop and isinstance(model_prop["examples"], list):
                models.extend(model_prop["examples"])
            if "enum" in model_prop and isinstance(model_prop["enum"], list):
                models.extend(model_prop["enum"])
            if "default" in model_prop and model_prop["default"]:
                models.append(model_prop["default"])

            # If no model property found, check voice or other descriptor
            if not models:
                voice_prop = schema.get("properties", {}).get("voice", {})
                if "examples" in voice_prop and isinstance(voice_prop["examples"], list):
                    models.extend(voice_prop["examples"])
                elif "default" in voice_prop and voice_prop["default"]:
                    models.append(voice_prop["default"])

            seen = set()
            unique_models = []
            for m in models:
                if isinstance(m, str) and m not in seen:
                    seen.add(m)
                    unique_models.append(m)

            # Extract voice options for TTS services
            voices = []
            voice_prop = schema.get("properties", {}).get("voice", {})
            if "examples" in voice_prop and isinstance(voice_prop["examples"], list):
                voices.extend(voice_prop["examples"])
            if "default" in voice_prop and voice_prop["default"]:
                voices.append(voice_prop["default"])

            seen_voices = set()
            unique_voices = []
            for v in voices:
                if isinstance(v, str) and v not in seen_voices:
                    seen_voices.add(v)
                    unique_voices.append(v)

            default_voice = (
                voice_prop.get("default")
                if isinstance(voice_prop.get("default"), str)
                else (unique_voices[0] if unique_voices else None)
            )

            title = schema.get("title") or prov_key.title()
            for suffix in ("TTSConfiguration", "STTConfiguration", "LLMConfiguration", "TTSService", "STTService", "LLMService", "Configuration", "Service"):
                if title.endswith(suffix) and len(title) > len(suffix):
                    title = title[:-len(suffix)].strip()
                    break

            docs_url = schema.get("provider_docs_url") or schema.get("json_schema_extra", {}).get("provider_docs_url")
            result.append({
                "value": prov_key,
                "label": title,
                "models": unique_models,
                "voices": unique_voices,
                "default_voice": default_voice,
                "docsUrl": docs_url,
                "docs_url": docs_url,
            })
        return result

    return {
        "llm": extract_provider_info(ServiceType.LLM),
        "stt": extract_provider_info(ServiceType.STT),
        "tts": extract_provider_info(ServiceType.TTS),
    }


@router.get("/master-keys", response_model=List[MasterKeyResponse])
@router.get("/platform/master-keys", response_model=List[MasterKeyResponse])
async def list_master_keys(
    service_type: Optional[str] = Query(None, description="Filter by service type (llm, stt, tts)"),
    current_user: UserModel = Depends(get_superuser),
):
    """List all platform master keys with masked secrets."""
    keys = await db_client.list_master_keys(service_type=service_type)
    return [
        MasterKeyResponse(
            id=k.id,
            service_type=k.service_type,
            provider=k.provider,
            key_prefix=k.key_prefix,
            is_default=k.is_default,
            default_model=k.default_model,
            default_voice=getattr(k, "default_voice", None),
            is_active=k.is_active,
            models_pricing=k.models_pricing or {},
            created_at=k.created_at,
            updated_at=k.updated_at,
        )
        for k in keys
    ]


@router.post("/master-keys", response_model=MasterKeyResponse)
@router.post("/platform/master-keys", response_model=MasterKeyResponse)
async def create_master_key(
    request: MasterKeyCreateRequest,
    current_user: UserModel = Depends(get_superuser),
):
    """Add a new platform master key."""
    if request.service_type not in ("llm", "stt", "tts"):
        raise HTTPException(status_code=400, detail="Invalid service_type. Must be 'llm', 'stt', or 'tts'.")

    created = await db_client.create_master_key(
        service_type=request.service_type,
        provider=request.provider.lower().strip(),
        api_key=request.api_key.strip(),
        is_default=request.is_default,
        default_model=request.default_model.strip() if request.default_model else None,
        default_voice=request.default_voice.strip() if request.default_voice else None,
        is_active=request.is_active,
        models_pricing=request.models_pricing or {},
    )
    await refresh_master_keys_cache()
    return MasterKeyResponse(
        id=created.id,
        service_type=created.service_type,
        provider=created.provider,
        key_prefix=created.key_prefix,
        is_default=created.is_default,
        default_model=created.default_model,
        default_voice=getattr(created, "default_voice", None),
        is_active=created.is_active,
        models_pricing=created.models_pricing or {},
        created_at=created.created_at,
        updated_at=created.updated_at,
    )


@router.put("/master-keys/{key_id}", response_model=MasterKeyResponse)
@router.put("/platform/master-keys/{key_id}", response_model=MasterKeyResponse)
async def update_master_key(
    key_id: int,
    request: MasterKeyUpdateRequest,
    current_user: UserModel = Depends(get_superuser),
):
    """Update a platform master key or its pricing models."""
    updated = await db_client.update_master_key(
        key_id=key_id,
        api_key=request.api_key.strip() if request.api_key else None,
        is_default=request.is_default,
        default_model=request.default_model.strip() if request.default_model else None,
        default_voice=request.default_voice.strip() if request.default_voice else None,
        is_active=request.is_active,
        models_pricing=request.models_pricing,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Master key not found")

    await refresh_master_keys_cache()
    return MasterKeyResponse(
        id=updated.id,
        service_type=updated.service_type,
        provider=updated.provider,
        key_prefix=updated.key_prefix,
        is_default=updated.is_default,
        default_model=updated.default_model,
        default_voice=getattr(updated, "default_voice", None),
        is_active=updated.is_active,
        models_pricing=updated.models_pricing or {},
        created_at=updated.created_at,
        updated_at=updated.updated_at,
    )


@router.post("/master-keys/{key_id}/set-default", response_model=MasterKeyResponse)
@router.post("/platform/master-keys/{key_id}/set-default", response_model=MasterKeyResponse)
async def set_default_master_key(
    key_id: int,
    current_user: UserModel = Depends(get_superuser),
):
    """Set a master key as default for its service type."""
    updated = await db_client.set_default_master_key(key_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Master key not found")

    await refresh_master_keys_cache()
    return MasterKeyResponse(
        id=updated.id,
        service_type=updated.service_type,
        provider=updated.provider,
        key_prefix=updated.key_prefix,
        is_default=updated.is_default,
        default_model=updated.default_model,
        default_voice=getattr(updated, "default_voice", None),
        is_active=updated.is_active,
        models_pricing=updated.models_pricing or {},
        created_at=updated.created_at,
        updated_at=updated.updated_at,
    )


@router.post("/master-keys/{key_id}/default-model", response_model=MasterKeyResponse)
@router.post("/platform/master-keys/{key_id}/default-model", response_model=MasterKeyResponse)
async def set_master_key_default_model(
    key_id: int,
    request: MasterKeySetDefaultModelRequest,
    current_user: UserModel = Depends(get_superuser),
):
    """Update the default model for a platform master key."""
    updated = await db_client.set_default_model(key_id, request.default_model.strip())
    if not updated:
        raise HTTPException(status_code=404, detail="Master key not found")

    await refresh_master_keys_cache()
    return MasterKeyResponse(
        id=updated.id,
        service_type=updated.service_type,
        provider=updated.provider,
        key_prefix=updated.key_prefix,
        is_default=updated.is_default,
        default_model=updated.default_model,
        default_voice=getattr(updated, "default_voice", None),
        is_active=updated.is_active,
        models_pricing=updated.models_pricing or {},
        created_at=updated.created_at,
        updated_at=updated.updated_at,
    )


@router.post("/master-keys/{key_id}/default-voice", response_model=MasterKeyResponse)
@router.post("/platform/master-keys/{key_id}/default-voice", response_model=MasterKeyResponse)
async def set_master_key_default_voice(
    key_id: int,
    request: MasterKeySetDefaultVoiceRequest,
    current_user: UserModel = Depends(get_superuser),
):
    """Update the default voice for a platform master key."""
    updated = await db_client.set_default_voice(key_id, request.default_voice.strip())
    if not updated:
        raise HTTPException(status_code=404, detail="Master key not found")

    await refresh_master_keys_cache()
    return MasterKeyResponse(
        id=updated.id,
        service_type=updated.service_type,
        provider=updated.provider,
        key_prefix=updated.key_prefix,
        is_default=updated.is_default,
        default_model=updated.default_model,
        default_voice=getattr(updated, "default_voice", None),
        is_active=updated.is_active,
        models_pricing=updated.models_pricing or {},
        created_at=updated.created_at,
        updated_at=updated.updated_at,
    )


@router.delete("/master-keys/{key_id}")
@router.delete("/platform/master-keys/{key_id}")
async def delete_master_key(
    key_id: int,
    current_user: UserModel = Depends(get_superuser),
):
    """Delete a platform master key."""
    deleted = await db_client.delete_master_key(key_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Master key not found")
    await refresh_master_keys_cache()
    return {"message": "Master key deleted successfully"}


# =========================================================================
# Platform Telephony Inventory Management
# =========================================================================

class StockNumberItem(BaseModel):
    address: str
    country_code: Optional[str] = "US"
    pool_type: str = "dedicated"  # 'dedicated' or 'shared_trial'
    monthly_price_cents: int = 0
    label: Optional[str] = None


class PlatformTelephonyInventoryCreateRequest(BaseModel):
    telephony_configuration_id: Optional[int] = None
    name: Optional[str] = None
    provider: Optional[str] = None  # 'twilio', 'telnyx', 'plivo', 'vonage', etc.
    credentials: Optional[dict] = None
    numbers: List[StockNumberItem]


@router.get("/telephony/inventory")
@router.get("/platform/inventory")
async def list_telephony_inventory(
    current_user: UserModel = Depends(get_superuser),
):
    """List all numbers stocked in the platform inventory."""
    return await db_client.list_all_platform_inventory()


@router.post("/telephony/inventory")
@router.post("/platform/inventory")
async def add_telephony_inventory(
    request: PlatformTelephonyInventoryCreateRequest,
    current_user: UserModel = Depends(get_superuser),
):
    """Add numbers as platform inventory to an existing or new telephony configuration."""
    if not current_user.selected_organization_id:
        raise HTTPException(status_code=400, detail="Superuser must have a selected organization")

    async with db_client.async_session() as session:
        from api.db.models import TelephonyConfigurationModel, TelephonyPhoneNumberModel
        from api.utils.telephony_address import normalize_telephony_address

        if request.telephony_configuration_id:
            cfg = await session.get(TelephonyConfigurationModel, request.telephony_configuration_id)
            if not cfg:
                raise HTTPException(status_code=404, detail="Telephony configuration not found")
            config_id = cfg.id
            provider_name = cfg.provider
            config_name = cfg.name
            cfg.is_platform_inventory = True
        else:
            if not request.name or not request.provider or request.credentials is None:
                raise HTTPException(
                    status_code=400,
                    detail="Either telephony_configuration_id or (name, provider, credentials) must be provided",
                )
            from api.routes.organization import _run_preprocess_hook

            creds = await _run_preprocess_hook(request.provider, request.credentials)
            new_config = await db_client.create_telephony_configuration(
                organization_id=current_user.selected_organization_id,
                name=request.name,
                provider=request.provider,
                credentials=creds,
                is_default_outbound=False,
            )
            config_id = new_config.id
            provider_name = request.provider
            config_name = request.name
            cfg = await session.get(TelephonyConfigurationModel, config_id)
            if cfg:
                cfg.is_platform_inventory = True

        from sqlalchemy import select

        created_numbers = []
        for item in request.numbers:
            normalized = normalize_telephony_address(item.address, country_hint=item.country_code)
            existing = (
                await session.execute(
                    select(TelephonyPhoneNumberModel).where(
                        TelephonyPhoneNumberModel.organization_id == current_user.selected_organization_id,
                        TelephonyPhoneNumberModel.address_normalized == normalized.canonical,
                    )
                )
            ).scalar_one_or_none()

            if existing:
                existing.telephony_configuration_id = config_id
                existing.is_platform_inventory = True
                existing.pool_type = item.pool_type
                existing.monthly_price_cents = item.monthly_price_cents
                if item.label:
                    existing.label = item.label
                existing.is_active = True
                created_numbers.append(item.address)
            else:
                num_row = TelephonyPhoneNumberModel(
                    organization_id=current_user.selected_organization_id,
                    telephony_configuration_id=config_id,
                    address=item.address,
                    address_normalized=normalized.canonical,
                    address_type=normalized.address_type,
                    country_code=item.country_code or normalized.country_code,
                    label=item.label or f"Platform {item.address}",
                    is_active=True,
                    is_default_caller_id=False,
                    is_platform_inventory=True,
                    pool_type=item.pool_type,
                    monthly_price_cents=item.monthly_price_cents,
                )
                session.add(num_row)
                created_numbers.append(item.address)

        await session.commit()

    return {
        "telephony_configuration_id": config_id,
        "name": config_name,
        "provider": provider_name,
        "numbers_added": created_numbers,
        "message": f"Successfully stocked {len(created_numbers)} numbers into platform inventory!",
    }


@router.delete("/telephony/inventory/{phone_number_id}")
@router.delete("/platform/inventory/{phone_number_id}")
async def delete_platform_number(
    phone_number_id: int,
    current_user: UserModel = Depends(get_superuser),
):
    """Delete a number from the platform inventory."""
    async with db_client.async_session() as session:
        from api.db.models import TelephonyPhoneNumberModel
        num = await session.get(TelephonyPhoneNumberModel, phone_number_id)
        if not num or not num.is_platform_inventory:
            raise HTTPException(status_code=404, detail="Platform number not found")

        await session.delete(num)
        await session.commit()

    return {"message": "Platform number removed from inventory"}


class CreditGrantRequest(BaseModel):
    amount_usd: float
    description: Optional[str] = "Admin manual credit grant"


@router.get("/organizations")
async def list_organizations_admin(
    current_user: UserModel = Depends(get_superuser),
):
    """List all organizations with current wallet balances."""
    orgs = await db_client.list_all_organizations()
    results = []
    for org in orgs:
        results.append(
            {
                "id": org.id,
                "provider_id": org.provider_id,
                "wallet_balance_usd": float(org.wallet_balance_usd or 0.0),
                "created_at": org.created_at.isoformat() if org.created_at else None,
            }
        )
    return results


@router.post("/organizations/{organization_id}/credits")
async def grant_organization_credits(
    organization_id: int,
    request: CreditGrantRequest,
    current_user: UserModel = Depends(get_superuser),
):
    """Add or adjust credits for an organization's platform wallet."""
    org = await db_client.get_organization_by_id(organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    new_balance = await db_client.update_wallet_balance(organization_id, request.amount_usd)
    return {
        "organization_id": organization_id,
        "delta_usd": request.amount_usd,
        "new_balance_usd": new_balance,
        "message": f"Successfully updated wallet balance to ${new_balance:.4f}",
    }

