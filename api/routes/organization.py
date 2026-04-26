from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.constants import DEFAULT_CAMPAIGN_RETRY_CONFIG, DEFAULT_ORG_CONCURRENCY_LIMIT
from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationConfigurationKey, PostHogEvent
from api.schemas.telephony_config import (
    TelephonyConfigRequest,
    TelephonyConfigurationResponse,
)
from api.services.auth.depends import get_user
from api.services.configuration.masking import is_mask_of, mask_key
from api.services.posthog_client import capture_event
from api.services.telephony import registry as telephony_registry
from api.services.worker_sync.manager import get_worker_sync_manager
from api.services.worker_sync.protocol import WorkerSyncEventType

router = APIRouter(prefix="/organizations", tags=["organizations"])


def _sensitive_fields(provider_name: str) -> List[str]:
    """Field names that should be masked when displaying stored config.

    Sourced from ProviderUIField.sensitive in the registry — the same source
    of truth that drives the form-rendering UI.
    """
    spec = telephony_registry.get_optional(provider_name)
    if spec is None or spec.ui_metadata is None:
        return []
    return [f.name for f in spec.ui_metadata.fields if f.sensitive]


def _mask_sensitive(provider_name: str, value: dict) -> dict:
    """Return a copy of ``value`` with sensitive fields masked for display."""
    out = dict(value)
    for field_name in _sensitive_fields(provider_name):
        v = out.get(field_name)
        if v:
            out[field_name] = mask_key(v)
    return out


class TelephonyProviderUIField(BaseModel):
    """One form field on a telephony provider's configuration UI."""

    name: str
    label: str
    type: str
    required: bool
    sensitive: bool
    description: Optional[str] = None
    placeholder: Optional[str] = None


class TelephonyProviderMetadata(BaseModel):
    """UI form metadata for a single telephony provider."""

    provider: str
    display_name: str
    fields: List[TelephonyProviderUIField]
    docs_url: Optional[str] = None


class TelephonyProvidersMetadataResponse(BaseModel):
    """List of UI form definitions used by the telephony-config screen."""

    providers: List[TelephonyProviderMetadata]


@router.get(
    "/telephony-providers/metadata",
    response_model=TelephonyProvidersMetadataResponse,
)
async def get_telephony_providers_metadata(user: UserModel = Depends(get_user)):
    """Return the list of available telephony providers and their form schemas.

    The UI uses this to render the configuration form generically instead of
    hard-coding fields per provider. Adding a new provider only requires
    declaring its ui_metadata in providers/<name>/__init__.py.
    """
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    providers = []
    for spec in telephony_registry.all_specs():
        if spec.ui_metadata is None:
            continue
        providers.append(
            TelephonyProviderMetadata(
                provider=spec.name,
                display_name=spec.ui_metadata.display_name,
                fields=[
                    TelephonyProviderUIField(
                        name=f.name,
                        label=f.label,
                        type=f.type,
                        required=f.required,
                        sensitive=f.sensitive,
                        description=f.description,
                        placeholder=f.placeholder,
                    )
                    for f in spec.ui_metadata.fields
                ],
                docs_url=spec.ui_metadata.docs_url,
            )
        )
    return TelephonyProvidersMetadataResponse(providers=providers)


@router.get("/telephony-config", response_model=TelephonyConfigurationResponse)
async def get_telephony_configuration(user: UserModel = Depends(get_user)):
    """Return telephony configuration for the user's org with sensitive fields masked."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    config = await db_client.get_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.TELEPHONY_CONFIGURATION.value,
    )

    if not config or not config.value:
        return TelephonyConfigurationResponse()

    stored_provider = config.value.get("provider", "twilio")
    spec = telephony_registry.get_optional(stored_provider)
    if spec is None:
        return TelephonyConfigurationResponse()

    masked = _mask_sensitive(stored_provider, config.value)
    response_obj = spec.config_response_cls.model_validate(masked)
    return TelephonyConfigurationResponse(**{stored_provider: response_obj})


@router.post("/telephony-config")
async def save_telephony_configuration(
    request: TelephonyConfigRequest,
    user: UserModel = Depends(get_user),
):
    """Save telephony configuration for the user's organization."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    existing_config = await db_client.get_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.TELEPHONY_CONFIGURATION.value,
    )

    config_value = request.model_dump()

    if existing_config and existing_config.value:
        if existing_config.value.get("provider") == request.provider:
            preserve_masked_fields(request, existing_config, config_value)

    await db_client.upsert_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.TELEPHONY_CONFIGURATION.value,
        config_value,
    )

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.TELEPHONY_CONFIGURED,
        properties={
            "provider": request.provider,
            "phone_number_count": len(request.from_numbers),
            "organization_id": user.selected_organization_id,
        },
    )

    return {"message": "Telephony configuration saved successfully"}


def preserve_masked_fields(request, existing_config, config_value):
    """If the client re-submits a masked sensitive field, restore the stored value."""
    for field_name in _sensitive_fields(request.provider):
        if hasattr(request, field_name):
            field_value = getattr(request, field_name)
            if field_value and is_mask_of(
                field_value, existing_config.value.get(field_name, "")
            ):
                config_value[field_name] = existing_config.value[field_name]


class LangfuseCredentialsRequest(BaseModel):
    host: str
    public_key: str
    secret_key: str


class LangfuseCredentialsResponse(BaseModel):
    host: str = ""
    public_key: str = ""
    secret_key: str = ""
    configured: bool = False


@router.get("/langfuse-credentials", response_model=LangfuseCredentialsResponse)
async def get_langfuse_credentials(user: UserModel = Depends(get_user)):
    """Get Langfuse credentials for the user's organization with masked sensitive fields."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    config = await db_client.get_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.LANGFUSE_CREDENTIALS.value,
    )

    if not config or not config.value:
        return LangfuseCredentialsResponse()

    return LangfuseCredentialsResponse(
        host=config.value.get("host", ""),
        public_key=mask_key(config.value.get("public_key", "")),
        secret_key=mask_key(config.value.get("secret_key", "")),
        configured=True,
    )


@router.post("/langfuse-credentials")
async def save_langfuse_credentials(
    request: LangfuseCredentialsRequest,
    user: UserModel = Depends(get_user),
):
    """Save Langfuse credentials for the user's organization."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    existing_config = await db_client.get_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.LANGFUSE_CREDENTIALS.value,
    )

    config_value = {
        "host": request.host,
        "public_key": request.public_key,
        "secret_key": request.secret_key,
    }

    # Preserve masked fields
    if existing_config and existing_config.value:
        if is_mask_of(request.public_key, existing_config.value.get("public_key", "")):
            config_value["public_key"] = existing_config.value["public_key"]
        if is_mask_of(request.secret_key, existing_config.value.get("secret_key", "")):
            config_value["secret_key"] = existing_config.value["secret_key"]

    await db_client.upsert_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.LANGFUSE_CREDENTIALS.value,
        config_value,
    )

    # Broadcast to all workers so every process updates its in-memory exporter
    await get_worker_sync_manager().broadcast(
        WorkerSyncEventType.LANGFUSE_CREDENTIALS,
        action="update",
        org_id=user.selected_organization_id,
    )

    return {"message": "Langfuse credentials saved successfully"}


@router.delete("/langfuse-credentials")
async def delete_langfuse_credentials(user: UserModel = Depends(get_user)):
    """Delete Langfuse credentials for the user's organization."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    deleted = await db_client.delete_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.LANGFUSE_CREDENTIALS.value,
    )

    if not deleted:
        raise HTTPException(status_code=404, detail="No Langfuse credentials found")

    # Broadcast to all workers so every process removes its in-memory exporter
    await get_worker_sync_manager().broadcast(
        WorkerSyncEventType.LANGFUSE_CREDENTIALS,
        action="delete",
        org_id=user.selected_organization_id,
    )

    return {"message": "Langfuse credentials deleted successfully"}


class RetryConfigResponse(BaseModel):
    enabled: bool
    max_retries: int
    retry_delay_seconds: int
    retry_on_busy: bool
    retry_on_no_answer: bool
    retry_on_voicemail: bool


class TimeSlotResponse(BaseModel):
    day_of_week: int
    start_time: str
    end_time: str


class ScheduleConfigResponse(BaseModel):
    enabled: bool
    timezone: str
    slots: List[TimeSlotResponse]


class CircuitBreakerConfigResponse(BaseModel):
    enabled: bool = False
    failure_threshold: float = 0.5
    window_seconds: int = 120
    min_calls_in_window: int = 5


class LastCampaignSettingsResponse(BaseModel):
    retry_config: Optional[RetryConfigResponse] = None
    max_concurrency: Optional[int] = None
    schedule_config: Optional[ScheduleConfigResponse] = None
    circuit_breaker: Optional[CircuitBreakerConfigResponse] = None


class CampaignDefaultsResponse(BaseModel):
    concurrent_call_limit: int
    from_numbers_count: int
    default_retry_config: RetryConfigResponse
    last_campaign_settings: Optional[LastCampaignSettingsResponse] = None


@router.get("/campaign-defaults", response_model=CampaignDefaultsResponse)
async def get_campaign_defaults(user: UserModel = Depends(get_user)):
    """Get campaign limits for the user's organization.

    Returns the organization's concurrent call limit and default retry configuration.
    """
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    # Get concurrent call limit
    concurrent_limit = DEFAULT_ORG_CONCURRENCY_LIMIT
    try:
        config = await db_client.get_configuration(
            user.selected_organization_id,
            OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT.value,
        )
        if config and config.value:
            concurrent_limit = int(
                config.value.get("value", DEFAULT_ORG_CONCURRENCY_LIMIT)
            )
    except Exception:
        pass

    # Get from_numbers count from telephony configuration
    from_numbers_count = 0
    try:
        telephony_config = await db_client.get_configuration(
            user.selected_organization_id,
            OrganizationConfigurationKey.TELEPHONY_CONFIGURATION.value,
        )
        if telephony_config and telephony_config.value:
            from_numbers = telephony_config.value.get("from_numbers", [])
            from_numbers_count = len(from_numbers)
    except Exception:
        pass

    # Get last campaign settings for pre-population
    last_campaign_settings = None
    try:
        last_campaign = await db_client.get_latest_campaign(
            user.selected_organization_id
        )
        if last_campaign:
            retry = None
            if last_campaign.retry_config:
                retry = RetryConfigResponse(**last_campaign.retry_config)

            max_conc = None
            sched = None
            cb = CircuitBreakerConfigResponse()
            if last_campaign.orchestrator_metadata:
                max_conc = last_campaign.orchestrator_metadata.get("max_concurrency")
                sc = last_campaign.orchestrator_metadata.get("schedule_config")
                if sc:
                    sched = ScheduleConfigResponse(
                        enabled=sc.get("enabled", False),
                        timezone=sc.get("timezone", "UTC"),
                        slots=[
                            TimeSlotResponse(**slot) for slot in sc.get("slots", [])
                        ],
                    )
                cb_data = last_campaign.orchestrator_metadata.get("circuit_breaker")
                if cb_data:
                    cb = CircuitBreakerConfigResponse(**cb_data)
                else:
                    cb = CircuitBreakerConfigResponse()

            last_campaign_settings = LastCampaignSettingsResponse(
                retry_config=retry,
                max_concurrency=max_conc,
                schedule_config=sched,
                circuit_breaker=cb,
            )
    except Exception:
        pass

    return CampaignDefaultsResponse(
        concurrent_call_limit=concurrent_limit,
        from_numbers_count=from_numbers_count,
        default_retry_config=RetryConfigResponse(**DEFAULT_CAMPAIGN_RETRY_CONFIG),
        last_campaign_settings=last_campaign_settings,
    )
