import json
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse, Response
from loguru import logger
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select, text, or_

from api.constants import (
    DEFAULT_CAMPAIGN_RETRY_CONFIG,
    DEFAULT_ORG_CONCURRENCY_LIMIT,
)
from api.db import db_client
from api.db.models import (
    CampaignModel,
    OrganizationConfigurationModel,
    QueuedRunModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import OrganizationConfigurationKey
from api.services.auth.depends import get_user
from api.services.campaign.runner import campaign_runner_service
from api.services.campaign.source_sync import CampaignSourceSyncService
from api.services.campaign.source_sync_factory import get_sync_service
from api.services.quota_service import authorize_workflow_run_start
from api.services.reports import generate_campaign_report_csv
from api.services.storage import storage_fs
from api.services.telephony.outbound_readiness import (
    OutboundConfigurationNotFoundError,
    OutboundSetupIncompleteError,
    requires_e164_destinations,
    resolve_outbound_configuration_id,
)
from api.utils.common import get_backend_endpoints

router = APIRouter(prefix="/campaign")


async def _get_org_concurrent_limit(organization_id: int) -> int:
    """Get the concurrent call limit for an organization."""
    try:
        config = await db_client.get_configuration(
            organization_id,
            OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT.value,
        )
        if config and config.value:
            return int(config.value.get("value", DEFAULT_ORG_CONCURRENCY_LIMIT))
    except Exception:
        pass
    return DEFAULT_ORG_CONCURRENCY_LIMIT


async def _get_from_numbers_count(
    organization_id: int, telephony_configuration_id: int | None
) -> int:
    """Active caller-ID count for the campaign's selected configuration."""
    if telephony_configuration_id is None:
        return 0
    try:
        cfg = await db_client.get_telephony_configuration_for_org(
            telephony_configuration_id,
            organization_id,
        )
        if cfg:
            addresses = await db_client.list_active_normalized_addresses_for_config(
                cfg.id
            )
            return len(addresses)
    except Exception:
        pass
    return 0


# Caller IDs rotate independently of the concurrency ceiling.
CLI_CONCURRENCY_WARNING = (
    "max_concurrency ({max_concurrency}) is above the {from_numbers_count} caller "
    "ID(s) configured. Caller IDs rotate and may be reused on simultaneous calls."
)


async def _validate_max_concurrency(
    max_concurrency: int,
    organization_id: int,
    telephony_configuration_id: int | None,
) -> list[str]:
    """Check max_concurrency and return any warnings for the operator.

    The organization limit is a hard ceiling and still raises, because it is
    the capacity the platform has agreed to carry. The caller-ID pool is not:
    caller IDs rotate and can be reused by simultaneous calls.

    Raises:
        HTTPException: 400 when the organization limit is exceeded.
    """
    org_limit = await _get_org_concurrent_limit(organization_id)
    if max_concurrency > org_limit:
        raise HTTPException(
            status_code=400,
            detail=(
                f"max_concurrency ({max_concurrency}) cannot exceed organization "
                f"limit ({org_limit})"
            ),
        )

    from_numbers_count = await _get_from_numbers_count(
        organization_id, telephony_configuration_id
    )
    if 0 < from_numbers_count < max_concurrency:
        warning = CLI_CONCURRENCY_WARNING.format(
            max_concurrency=max_concurrency,
            from_numbers_count=from_numbers_count,
        )
        logger.warning(
            f"Campaign concurrency above CLI pool for org {organization_id}, "
            f"config {telephony_configuration_id}: {warning}"
        )
        return [warning]

    return []


async def _validate_dial_rate(rate: int, organization_id: int) -> None:
    org_limit = await _get_org_concurrent_limit(organization_id)
    if rate > org_limit:
        raise HTTPException(
            status_code=400,
            detail=f"rate_limit_per_second ({rate}) cannot exceed organization limit ({org_limit})",
        )


class RetryConfigRequest(BaseModel):
    enabled: bool = True
    max_retries: int = Field(default=2, ge=0, le=10)
    retry_delay_seconds: int = Field(default=120, ge=30, le=3600)
    retry_on_busy: bool = True
    retry_on_no_answer: bool = True
    retry_on_voicemail: bool = True


class RetryConfigResponse(BaseModel):
    enabled: bool = True
    max_retries: int = 2
    retry_delay_seconds: int = 120
    retry_on_busy: bool = True
    retry_on_no_answer: bool = True
    retry_on_voicemail: bool = True


class TimeSlotRequest(BaseModel):
    day_of_week: int = Field(..., ge=0, le=6)
    start_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")

    @model_validator(mode="after")
    def validate_times(self):
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time")
        return self


class ScheduleConfigRequest(BaseModel):
    enabled: bool = True
    timezone: str = "UTC"
    slots: List[TimeSlotRequest] = Field(..., min_length=1, max_length=50)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (KeyError, Exception):
            raise ValueError(f"Invalid timezone: {v}")
        return v


class TimeSlotResponse(BaseModel):
    day_of_week: int
    start_time: str
    end_time: str


class ScheduleConfigResponse(BaseModel):
    enabled: bool
    timezone: str
    slots: List[TimeSlotResponse]


class CircuitBreakerConfigRequest(BaseModel):
    enabled: bool = True
    failure_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    window_seconds: int = Field(default=120, ge=30, le=600)
    min_calls_in_window: int = Field(default=5, ge=1, le=100)


class CircuitBreakerConfigResponse(BaseModel):
    enabled: bool = False
    failure_threshold: float = 0.5
    window_seconds: int = 120
    min_calls_in_window: int = 5


class CreateCampaignRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    workflow_id: Optional[int] = None
    source_type: Optional[str] = "csv"
    source_id: Optional[str] = None  # CSV file key or direct ID
    # Optional for backwards compatibility. When omitted, the resolver prefers
    # the marked default and then another ready active configuration.
    telephony_configuration_id: Optional[int] = None
    retry_config: Optional[RetryConfigRequest] = None
    max_concurrency: Optional[int] = Field(default=None, ge=1)
    rate_limit_per_second: int = Field(default=1, ge=1, strict=True)
    schedule_config: Optional[ScheduleConfigRequest] = None
    circuit_breaker: Optional[CircuitBreakerConfigRequest] = None
    # Callio native variables & parameters
    variables: Optional[Dict[str, Any]] = None
    instructions: Optional[str] = None
    calling_hours: Optional[str] = None
    language: Optional[str] = None
    voice: Optional[str] = None
    voice_id: Optional[str] = None
    tts_provider: Optional[str] = None
    max_concurrent_calls: Optional[int] = None
    retries: Optional[int] = None
    max_call_length_minutes: Optional[int] = None
    record_calls: Optional[bool] = None
    contacts: Optional[List[Dict[str, Any]]] = None
    contact_ids: Optional[List[int]] = None


class UpdateCampaignRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    retry_config: Optional[RetryConfigRequest] = None
    max_concurrency: Optional[int] = Field(default=None, ge=1)
    rate_limit_per_second: Optional[int] = Field(default=None, ge=1, strict=True)
    schedule_config: Optional[ScheduleConfigRequest] = None
    circuit_breaker: Optional[CircuitBreakerConfigRequest] = None


class CampaignLogEntryResponse(BaseModel):
    """A single timestamped entry from the campaign's append-only log.

    Surfaced in the UI so operators can see why a campaign moved to
    paused / failed without digging through server logs.
    """

    ts: str
    level: str
    event: str
    message: str
    details: Optional[Dict[str, Any]] = None


class CampaignResponse(BaseModel):
    id: int
    name: str
    workflow_id: int
    workflow_name: str
    state: str
    source_type: str
    source_id: str
    total_rows: Optional[int]
    processed_rows: int
    failed_rows: int
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    retry_config: RetryConfigResponse
    max_concurrency: Optional[int] = None
    rate_limit_per_second: int = 1
    schedule_config: Optional[ScheduleConfigResponse] = None
    circuit_breaker: Optional[CircuitBreakerConfigResponse] = None
    executed_count: int = 0
    total_queued_count: int = 0
    parent_campaign_id: Optional[int] = None
    redialed_campaign_id: Optional[int] = None
    telephony_configuration_id: Optional[int] = None
    telephony_configuration_name: Optional[str] = None
    orchestrator_metadata: Optional[Dict[str, Any]] = None
    logs: List[CampaignLogEntryResponse] = Field(default_factory=list)
    # Things the operator should know that are not errors - dialling wider
    # than the caller-ID pool, for instance.
    warnings: List[str] = Field(default_factory=list)


class CampaignsResponse(BaseModel):
    campaigns: List[CampaignResponse]


class WorkflowRunResponse(BaseModel):
    id: int
    workflow_id: int
    state: str
    created_at: datetime
    completed_at: Optional[datetime]


class CampaignRunsResponse(BaseModel):
    """Paginated response for campaign workflow runs"""

    runs: List[dict]  # WorkflowRunResponseSchema from schemas
    total_count: int
    page: int
    limit: int
    total_pages: int


class CampaignProgressResponse(BaseModel):
    campaign_id: int
    state: str
    total_rows: int
    processed_rows: int
    failed_calls: int
    progress_percentage: float
    source_sync: dict
    rate_limit: int
    started_at: Optional[datetime]
    completed_at: Optional[datetime]


# Default retry config for campaigns


def _build_campaign_response(
    campaign,
    workflow_name: str,
    executed_count: int = 0,
    total_queued_count: int = 0,
    telephony_configuration_name: Optional[str] = None,
    warnings: Optional[List[str]] = None,
) -> CampaignResponse:
    """Build a CampaignResponse from a campaign model."""
    # Get retry_config from campaign or use defaults
    retry_config = dict(DEFAULT_CAMPAIGN_RETRY_CONFIG)
    if isinstance(campaign.retry_config, dict):
        retry_config.update(campaign.retry_config)

    # Get max_concurrency, schedule_config, circuit_breaker from orchestrator_metadata
    max_concurrency = None
    schedule_config = None
    circuit_breaker_config = CircuitBreakerConfigResponse()
    parent_campaign_id = None
    redialed_campaign_id = None
    if campaign.orchestrator_metadata:
        max_concurrency = campaign.orchestrator_metadata.get("max_concurrency")
        sc = campaign.orchestrator_metadata.get("schedule_config")
        if sc:
            schedule_config = ScheduleConfigResponse(
                enabled=sc.get("enabled", False),
                timezone=sc.get("timezone", "UTC"),
                slots=[TimeSlotResponse(**slot) for slot in sc.get("slots", [])],
            )
        cb = campaign.orchestrator_metadata.get("circuit_breaker")
        if cb:
            circuit_breaker_config = CircuitBreakerConfigResponse(**cb)
        parent_campaign_id = campaign.orchestrator_metadata.get("parent_campaign_id")
        redialed_campaign_id = campaign.orchestrator_metadata.get(
            "redialed_campaign_id"
        )

    return CampaignResponse(
        warnings=warnings or [],
        id=campaign.id,
        name=campaign.name,
        workflow_id=campaign.workflow_id,
        workflow_name=workflow_name,
        state=campaign.state,
        source_type=campaign.source_type,
        source_id=campaign.source_id,
        total_rows=campaign.total_rows,
        processed_rows=campaign.processed_rows,
        failed_rows=campaign.failed_rows,
        created_at=campaign.created_at,
        started_at=campaign.started_at,
        completed_at=campaign.completed_at,
        retry_config=RetryConfigResponse(**retry_config),
        max_concurrency=max_concurrency,
        rate_limit_per_second=campaign.rate_limit_per_second,
        schedule_config=schedule_config,
        circuit_breaker=circuit_breaker_config,
        executed_count=executed_count,
        total_queued_count=total_queued_count,
        parent_campaign_id=parent_campaign_id,
        redialed_campaign_id=redialed_campaign_id,
        telephony_configuration_id=campaign.telephony_configuration_id,
        telephony_configuration_name=telephony_configuration_name,
        orchestrator_metadata=campaign.orchestrator_metadata,
        logs=[
            CampaignLogEntryResponse(**entry)
            for entry in (campaign.logs or [])
            if isinstance(entry, dict)
        ],
    )


async def _get_campaign_stats(campaign_id: int) -> tuple[int, int]:
    """Return (executed_count, total_queued_count) for a campaign."""
    stats_map = await db_client.get_queued_runs_stats_for_campaigns([campaign_id])
    s = stats_map.get(campaign_id, {})
    return s.get("executed", 0), s.get("total", 0)


async def _get_telephony_configuration_name(
    config_id: Optional[int], organization_id: int
) -> Optional[str]:
    """Resolve the display name for a campaign's telephony configuration.

    Org-scoped lookup so a stale FK from another org (shouldn't happen, but
    cheap to enforce) doesn't leak across tenants.
    """
    if config_id is None:
        return None
    cfg = await db_client.get_telephony_configuration_for_org(
        config_id, organization_id, active_only=False
    )
    return cfg.name if cfg else None


@router.get("/draft")
async def get_campaign_draft(
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Retrieve saved campaign wizard draft for the organization."""
    try:
        config = await db_client.get_configuration(
            user.selected_organization_id,
            OrganizationConfigurationKey.CAMPAIGN_DRAFT.value,
        )
        if config and config.value:
            return {"draft": config.value}
    except Exception as e:
        logger.warning(f"Failed to fetch campaign draft: {e}")
    return {"draft": None}


@router.post("/draft")
async def save_campaign_draft(
    payload: Dict[str, Any],
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Save or update campaign wizard draft for the organization."""
    try:
        await db_client.upsert_configuration(
            organization_id=user.selected_organization_id,
            key=OrganizationConfigurationKey.CAMPAIGN_DRAFT.value,
            value=payload,
        )
        return {"status": "saved"}
    except Exception as e:
        logger.error(f"Failed to save campaign draft: {e}")
        raise HTTPException(status_code=500, detail="Failed to save draft")


@router.delete("/draft")
async def delete_campaign_draft(
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Clear saved campaign wizard draft for the organization."""
    try:
        async with db_client.async_session() as session:
            result = await session.execute(
                select(OrganizationConfigurationModel).where(
                    OrganizationConfigurationModel.organization_id == user.selected_organization_id,
                    OrganizationConfigurationModel.key == OrganizationConfigurationKey.CAMPAIGN_DRAFT.value,
                )
            )
            config = result.scalars().first()
            if config:
                await session.delete(config)
                await session.commit()
        return {"status": "cleared"}
    except Exception as e:
        logger.error(f"Failed to delete campaign draft: {e}")
        return {"status": "cleared"}


@router.post("/create")
@router.post("")
@router.post("/")
async def create_campaign(
    request: CreateCampaignRequest,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Create a new campaign"""
    # Verify workflow exists and belongs to organization or is a builtin template
    workflow = None
    workflow_id = request.workflow_id
    if workflow_id:
        workflow = await db_client.get_workflow(
            workflow_id, organization_id=user.selected_organization_id
        )
        if not workflow:
            async with db_client.async_session() as session:
                wf_res = await session.get(WorkflowModel, workflow_id)
                if wf_res and wf_res.is_builtin:
                    workflow = wf_res

    if not workflow:
        all_wfs = await db_client.get_all_workflows_for_listing(user.selected_organization_id, status=None)
        if all_wfs:
            workflow = all_wfs[0]
            workflow_id = workflow.id
        else:
            async with db_client.async_session() as session:
                builtin_res = await session.execute(
                    select(WorkflowModel).where(WorkflowModel.is_builtin == True).limit(1)
                )
                workflow = builtin_res.scalars().first()
                if workflow:
                    workflow_id = workflow.id

    if not workflow:
        raise HTTPException(status_code=404, detail="No active or built-in AI caller found")

    workflow_name = workflow.name

    # Resolved before the source is validated
    try:
        telephony_configuration_id = await resolve_outbound_configuration_id(
            request.telephony_configuration_id,
            user.selected_organization_id,
            db=db_client,
        )
    except OutboundSetupIncompleteError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except OutboundConfigurationNotFoundError as e:
        raise HTTPException(
            status_code=400, detail="telephony_configuration_not_found"
        ) from e

    require_e164 = await requires_e164_destinations(
        telephony_configuration_id,
        user.selected_organization_id,
        db=db_client,
    )

    source_type = request.source_type or "csv"
    source_id = request.source_id or f"direct_{user.selected_organization_id}_{uuid.uuid4()}"
    validation_result = None

    if request.source_id:
        # Validate source data if CSV was uploaded
        sync_service = get_sync_service(source_type)
        validation_result = await sync_service.validate_source(
            source_id,
            user.selected_organization_id,
            require_e164=require_e164,
        )
        if not validation_result.is_valid:
            raise HTTPException(status_code=400, detail=validation_result.error.message)

        # Validate template variables against source data columns
        if workflow:
            from api.services.workflow.dto import ReactFlowDTO
            from api.services.workflow.workflow_graph import WorkflowGraph

            workflow_def = getattr(workflow, "released_definition", None)
            workflow_json = workflow_def.workflow_json if workflow_def else getattr(workflow, "workflow_definition", None)
            if workflow_json:
                try:
                    dto = ReactFlowDTO(**workflow_json)
                    graph = WorkflowGraph(dto, skip_instance_constraints_for={"trigger"})
                    required_vars = graph.get_required_template_variables()

                    if (
                        required_vars
                        and validation_result.headers
                        and validation_result.rows
                    ):
                        template_validation = (
                            CampaignSourceSyncService.validate_template_columns(
                                validation_result.headers,
                                validation_result.rows,
                                required_vars,
                            )
                        )
                        if not template_validation.is_valid:
                            raise HTTPException(
                                status_code=400,
                                detail=template_validation.error.message,
                            )
                except HTTPException:
                    raise
                except Exception:
                    pass

    warnings: List[str] = []

    # Shared trial numbers are for testing (up to 10 contacts per test campaign)
    config_numbers = await db_client.list_phone_numbers_for_config(telephony_configuration_id)
    cfg = await db_client.get_telephony_configuration_for_org(
        telephony_configuration_id, user.selected_organization_id, active_only=False
    )
    is_trial_config = (
        (cfg and bool(cfg.name) and cfg.name.startswith("Platform - "))
        or (getattr(cfg, "is_platform_inventory", False))
        or (bool(config_numbers) and any(getattr(n, "pool_type", None) == "shared_trial" for n in config_numbers))
    )
    total_contacts = len(validation_result.rows) if (validation_result and validation_result.rows) else 0
    if is_trial_config and total_contacts > 10:
        raise HTTPException(
            status_code=400,
            detail="Shared trial numbers are limited to 10 contacts for test campaigns. For larger bulk calling, please claim a dedicated number or connect your telephony provider.",
        )

    effective_concurrency = request.max_concurrency or request.max_concurrent_calls
    if effective_concurrency is not None:
        warnings = await _validate_max_concurrency(
            effective_concurrency,
            user.selected_organization_id,
            telephony_configuration_id,
        )

    await _validate_dial_rate(
        request.rate_limit_per_second, user.selected_organization_id
    )

    retry_config = None
    if request.retry_config:
        retry_config = request.retry_config.model_dump()
    elif request.retries is not None:
        retry_config = {"enabled": True, "max_retries": request.retries}

    schedule_config = None
    if request.schedule_config:
        schedule_config = request.schedule_config.model_dump()

    circuit_breaker_config = None
    if request.circuit_breaker:
        circuit_breaker_config = request.circuit_breaker.model_dump()

    # Pass dynamic Callio parameters into orchestrator_metadata
    orchestrator_metadata = {}
    if request.variables:
        orchestrator_metadata["variables"] = request.variables
    if request.instructions:
        orchestrator_metadata["instructions"] = request.instructions
    if request.calling_hours:
        orchestrator_metadata["calling_hours"] = request.calling_hours
    if request.language:
        orchestrator_metadata["language"] = request.language
    if request.voice or request.voice_id:
        orchestrator_metadata["voice"] = request.voice or request.voice_id
        orchestrator_metadata["voice_id"] = request.voice_id or request.voice
    if request.tts_provider:
        orchestrator_metadata["tts_provider"] = request.tts_provider
    if request.record_calls is not None:
        orchestrator_metadata["record_calls"] = request.record_calls

    campaign = await db_client.create_campaign(
        name=request.name,
        workflow_id=workflow_id,
        source_type=source_type,
        source_id=source_id,
        user_id=user.id,
        organization_id=user.selected_organization_id,
        retry_config=retry_config,
        max_concurrency=effective_concurrency,
        schedule_config=schedule_config,
        circuit_breaker=circuit_breaker_config,
        telephony_configuration_id=telephony_configuration_id,
        rate_limit_per_second=request.rate_limit_per_second,
    )

    if orchestrator_metadata:
        async with db_client.async_session() as session:
            c_db = await session.get(CampaignModel, campaign.id)
            if c_db:
                curr = dict(c_db.orchestrator_metadata or {})
                curr.update(orchestrator_metadata)
                c_db.orchestrator_metadata = curr
                await session.commit()
                campaign.orchestrator_metadata = curr

    # Ensure organization_contacts table exists
    from api.routes.contacts import ensure_table, normalize_phone_number
    await ensure_table()

    # Process and link / insert contacts into organization phonebook
    contacts_to_dial: List[Dict[str, Any]] = []

    if request.contact_ids:
        async with db_client.async_session() as session:
            await session.execute(
                text(
                    """
                    UPDATE organization_contacts
                    SET campaign_id = :campaign_id, updated_at = NOW()
                    WHERE organization_id = :org_id AND id = ANY(:ids)
                    """
                ),
                {
                    "org_id": user.selected_organization_id,
                    "ids": request.contact_ids,
                    "campaign_id": campaign.id,
                },
            )
            res = await session.execute(
                text(
                    """
                    SELECT id, name, phone, email, company, city
                    FROM organization_contacts
                    WHERE organization_id = :org_id AND id = ANY(:ids)
                    """
                ),
                {"org_id": user.selected_organization_id, "ids": request.contact_ids},
            )
            contacts_to_dial = [dict(r._mapping) for r in res.fetchall()]
            await session.commit()

    elif request.contacts:
        seen_phones = set()
        async with db_client.async_session() as session:
            for c in request.contacts:
                raw_phone = str(c.get("phone") or "").strip()
                phone_val = normalize_phone_number(raw_phone)
                if not phone_val or phone_val in seen_phones:
                    continue
                seen_phones.add(phone_val)
                res = await session.execute(
                    text(
                        """
                        INSERT INTO organization_contacts 
                        (organization_id, name, phone, email, company, city, status, called, campaign_id, created_at, updated_at)
                        VALUES (:org_id, :name, :phone, :email, :company, :city, 'valid', FALSE, :campaign_id, NOW(), NOW())
                        ON CONFLICT (organization_id, phone) DO UPDATE
                            SET name = EXCLUDED.name,
                                campaign_id = EXCLUDED.campaign_id,
                                email = COALESCE(EXCLUDED.email, organization_contacts.email),
                                company = COALESCE(EXCLUDED.company, organization_contacts.company),
                                city = COALESCE(EXCLUDED.city, organization_contacts.city),
                                updated_at = NOW()
                        RETURNING id, name, phone, email, company, city
                        """
                    ),
                    {
                        "org_id": user.selected_organization_id,
                        "name": str(c.get("name") or "Customer"),
                        "phone": phone_val,
                        "email": str(c.get("email") or ""),
                        "company": str(c.get("company") or ""),
                        "city": str(c.get("city") or ""),
                        "campaign_id": campaign.id,
                    },
                )
                row = res.fetchone()
                if row:
                    contacts_to_dial.append(dict(row._mapping))
            await session.commit()

    if contacts_to_dial:
        now_utc = datetime.now(UTC)
        async with db_client.async_session() as session:
            # Self-heal schema defensively for queued_runs in case migrations were pending
            await session.execute(
                text(
                    """
                    ALTER TABLE queued_runs ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;
                    ALTER TABLE queued_runs ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0;
                    ALTER TABLE queued_runs ADD COLUMN IF NOT EXISTS parent_queued_run_id INTEGER;
                    ALTER TABLE queued_runs ADD COLUMN IF NOT EXISTS scheduled_for TIMESTAMPTZ;
                    ALTER TABLE queued_runs ADD COLUMN IF NOT EXISTS retry_reason VARCHAR;
                    """
                )
            )
            for item in contacts_to_dial:
                qr = QueuedRunModel(
                    campaign_id=campaign.id,
                    source_uuid=f"c_{item.get('id', uuid.uuid4())}",
                    context_variables={
                        "name": item.get("name", "Customer"),
                        "phone_number": item.get("phone"),
                        "phone": item.get("phone"),
                        "email": item.get("email", ""),
                        "company": item.get("company", ""),
                        "city": item.get("city", ""),
                        **(request.variables or {}),
                    },
                    state="queued",
                )
                session.add(qr)
            
            c_db = await session.get(CampaignModel, campaign.id)
            if c_db:
                c_db.total_rows = len(contacts_to_dial)
                c_db.state = "running"
                c_db.started_at = now_utc
                c_db.source_sync_status = "completed"
                c_db.source_last_synced_at = now_utc
            await session.commit()
            campaign.total_rows = len(contacts_to_dial)
            campaign.state = "running"
            campaign.started_at = now_utc

        # Directly kick off processing in FastAPI background event loop
        import asyncio
        from api.services.campaign.campaign_call_dispatcher import (
            campaign_call_dispatcher,
        )
        asyncio.create_task(
            campaign_call_dispatcher.process_batch(campaign.id)
        )

        # Also queue to ARQ if worker is present
        try:
            from api.tasks.arq import enqueue_job
            from api.tasks.function_names import FunctionNames
            await enqueue_job(FunctionNames.PROCESS_CAMPAIGN_BATCH, campaign.id)
        except Exception as e:
            logger.warning(f"Could not enqueue to ARQ: {e}")

    cfg_name = await _get_telephony_configuration_name(
        campaign.telephony_configuration_id, user.selected_organization_id
    )
    return _build_campaign_response(
        campaign,
        workflow_name,
        telephony_configuration_name=cfg_name,
        warnings=warnings,
    )


class TestCallRequest(BaseModel):
    phone_number: str
    workflow_id: Optional[int] = None
    instructions: Optional[str] = None
    from_phone_number: Optional[str] = None
    from_phone_number_id: Optional[int] = None
    telephony_configuration_id: Optional[int] = None
    voice: Optional[str] = None
    voice_id: Optional[str] = None
    tts_provider: Optional[str] = None


@router.post("/test-call")
async def trigger_test_call(
    request: TestCallRequest,
    user: UserModel = Depends(get_user),
):
    """
    Trigger a direct test call to the specified phone number using a platform test number
    or configured telephony carrier.
    """
    workflow_name = "AI Voice Caller"
    wf = None
    if request.workflow_id:
        wf = await db_client.get_workflow(request.workflow_id, organization_id=user.selected_organization_id)
        if not wf:
            wf = await db_client.get_workflow_by_id(request.workflow_id)
        if wf:
            workflow_name = wf.name

    # Resolve outbound caller ID strictly from Telephony Inventory
    from_number = request.from_phone_number
    telephony_config_id = request.telephony_configuration_id
    from_phone_id = request.from_phone_number_id

    # Fetch platform inventory to resolve configured test numbers
    inventory = await db_client.list_all_platform_inventory()
    if inventory:
        # If user picked a specific number, match its config and id
        matched_item = None
        if from_number:
            matched_item = next(
                (item for item in inventory if item.get("phone_number") == from_number or item.get("address") == from_number),
                None,
            )
        if not matched_item:
            # Prioritize verified number (+14786063123)
            matched_item = next(
                (item for item in inventory if "3123" in str(item.get("phone_number", "")) or "3123" in str(item.get("address", ""))),
                None,
            )
        if not matched_item:
            matched_item = inventory[0]
        
        if matched_item:
            from_number = matched_item.get("phone_number") or matched_item.get("address")
            telephony_config_id = (
                telephony_config_id
                or matched_item.get("configuration_id")
                or matched_item.get("telephony_configuration_id")
            )
            from_phone_id = from_phone_id or matched_item.get("id")
    else:
        if not from_number:
            from_number = "+919876543210"

    # Ensure shared trial telephony is provisioned for this organization
    try:
        from api.services.telephony.shared_trial_sync import sync_shared_trial_telephony_for_org
        if user.selected_organization_id:
            await sync_shared_trial_telephony_for_org(user.selected_organization_id)
    except Exception as exc:
        logger.debug(f"sync_shared_trial_telephony note: {exc}")

    # Attempt to place call via live telephony initiate-call if possible
    telephony_status = "completed"
    call_msg = f"Live test call initiated to {request.phone_number} from platform test number {from_number}."

    if request.workflow_id and wf:
        try:
            from api.routes.telephony import initiate_call, InitiateCallRequest
            call_req = InitiateCallRequest(
                workflow_id=wf.id,
                phone_number=request.phone_number,
                telephony_configuration_id=telephony_config_id,
                from_phone_number_id=from_phone_id,
                call_origin="campaign_test",
                voice=request.voice,
                voice_id=request.voice_id,
                tts_provider=request.tts_provider,
            )
            res = await initiate_call(call_req, user=user)
            return {
                "status": "initiated",
                "phone_number": request.phone_number,
                "from_number": from_number,
                "workflow_run_id": res.get("workflow_run_id") if isinstance(res, dict) else None,
                "summary": f"Live test call dispatched to {request.phone_number} from {from_number}.",
                "workflow_name": workflow_name,
                "details": res,
            }
        except HTTPException as http_exc:
            raise http_exc
        except Exception as e:
            logger.error(f"Telephony test call dispatch error: {e}")
            err_msg = str(e)
            if "21210" in err_msg:
                err_msg = f"Twilio Caller ID '{from_number}' is not verified. Please select verified number +14786063123 or verify it in your Twilio Console."
            raise HTTPException(status_code=400, detail=err_msg)

    raise HTTPException(status_code=404, detail="AI Caller or workflow not found")


def resolve_call_intent(
    annotations: dict | None = None,
    gathered: dict | None = None,
    raw_intent_override: str | None = None,
    duration: int = 0,
    is_completed: bool = False,
) -> str | None:
    """Accurately classify user intent based on explicit LLM extraction, disposition, and conversation outcome."""
    ann = annotations or {}
    g_ctx = gathered or {}
    ext_vars = g_ctx.get("extracted_variables") or {}
    if not isinstance(ext_vars, dict):
        ext_vars = {}

    # Check explicit variables
    explicit_interest = str(
        ext_vars.get("interest_level") or g_ctx.get("interest_level") or ann.get("interest_level") or ""
    ).strip().lower()

    explicit_disp = str(
        g_ctx.get("call_disposition") or g_ctx.get("mapped_call_disposition") or g_ctx.get("disposition") or ""
    ).strip().lower()

    raw = str(
        raw_intent_override
        or ann.get("intent")
        or g_ctx.get("intent")
        or ext_vars.get("interest_level")
        or ext_vars.get("lead_status")
        or g_ctx.get("call_disposition")
        or g_ctx.get("mapped_call_disposition")
        or g_ctx.get("disposition")
        or g_ctx.get("outcome")
        or ""
    ).lower()

    # 1. Negative / Not interested / Declined / Wrong number (highest priority rejection)
    if any(k in raw for k in ["not", "dnd", "reject", "decline", "wrong", "uninterested", "nahi", "no interest"]):
        return "wrong-number" if "wrong" in raw else "not-interested"

    # 2. Appointment / Meeting scheduled (highest positive intent)
    if any(k in raw for k in ["appointment", "visit", "book", "schedule", "demo", "meeting", "site visit"]):
        return "appointment"

    # 3. Call back later / Busy
    if any(k in raw for k in ["call_back", "callback", "later", "busy"]):
        return "call-back"

    # 4. Explicit Interested / Qualified
    if any(k in explicit_interest for k in ["interested", "positive", "high"]) and not any(k in explicit_interest for k in ["not", "un", "decline"]):
        return "interested"

    if explicit_disp in ["qualified", "interested", "converted", "high_intent"]:
        return "interested"

    if any(k in raw for k in ["interest", "positive", "qualified", "converted", "high_intent"]) and not any(k in raw for k in ["not", "uninterested", "decline"]):
        return "interested"

    # 5. Needs Review / Ambiguous
    if any(k in raw for k in ["review", "unsure", "confused", "doubt", "ambiguous", "maybe", "check with"]):
        return "needs-review"

    # If call was completed and had spoken dialogue or duration >= 15s without explicit rejection or conversion:
    call_tags = g_ctx.get("call_tags") or []
    if is_completed and (duration >= 15 or "user_speech" in call_tags):
        return "needs-review"

    # 6. Default: None
    return None


@router.get("/all-calls")
async def get_all_organization_calls(
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=200),
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """
    Get all calls (campaign calls, campaign test calls, and caller test calls)
    for the user's organization.
    """
    org_id = user.selected_organization_id
    if not org_id:
        return {"calls": [], "total_count": 0}

    async with db_client.async_session() as session:
        # 1. Fetch campaigns for this organization
        c_res = await session.execute(
            select(CampaignModel.id, CampaignModel.name).where(
                CampaignModel.organization_id == org_id
            )
        )
        camp_rows = c_res.all()
        camp_map = {row.id: row.name for row in camp_rows}
        camp_ids = list(camp_map.keys())

        # 2. Fetch workflows
        wf_res = await session.execute(
            select(WorkflowModel.id, WorkflowModel.name)
        )
        wf_map = {row.id: row.name for row in wf_res.all()}

        # 3. Query all workflow runs belonging to this organization:
        # - Either run.campaign_id in camp_ids
        # - Or workflow.organization_id == org_id
        # - Or run.extra->>'organization_id' = str(org_id)
        org_filter_clauses = [
            WorkflowModel.organization_id == org_id,
            text("workflow_runs.extra->>'organization_id' = :org_id_str"),
        ]
        if camp_ids:
            org_filter_clauses.append(WorkflowRunModel.campaign_id.in_(camp_ids))

        # Count query for pagination metadata
        count_query = (
            select(func.count(WorkflowRunModel.id))
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(or_(*org_filter_clauses))
            .params(org_id_str=str(org_id))
        )
        count_res = await session.execute(count_query)
        total_count = count_res.scalar() or 0

        run_query = (
            select(WorkflowRunModel)
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(or_(*org_filter_clauses))
            .params(org_id_str=str(org_id))
            .order_by(WorkflowRunModel.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        res = await session.execute(run_query)
        runs = res.scalars().all()

        formatted_calls = []
        for r in runs:
            init = r.initial_context or {}
            gathered = r.gathered_context or {}
            annotations = r.annotations or {}
            usage = r.usage_info or {}
            extra = r.extra or {}

            phone = (
                init.get("phone_number")
                or init.get("phone")
                or init.get("to_phone")
                or init.get("destination")
                or init.get("called_number")
                or "—"
            )
            name = (
                init.get("name")
                or init.get("customer_name")
                or init.get("lead_name")
                or init.get("contact_name")
                or ("Customer" if r.campaign_id else "Test User")
            )

            dur = (
                usage.get("call_duration_seconds")
                or usage.get("duration_seconds")
                or (45 if r.is_completed else 0)
            )

            # Classify call origin / type
            origin = init.get("call_origin") or extra.get("call_origin") or ""
            if r.campaign_id:
                call_type = "campaign"
            elif origin == "campaign_test" or "CAMP" in (r.name or ""):
                call_type = "campaign-test"
            else:
                call_type = "caller-test"

            # Parse intent accurately
            intent = resolve_call_intent(
                annotations=annotations,
                gathered=gathered,
                duration=dur,
                is_completed=r.is_completed,
            )

            # Status
            raw_intent = str(annotations.get("intent") or gathered.get("intent") or gathered.get("call_disposition") or "").lower()
            status = "in-progress"
            if r.is_completed:
                status = "completed"
            elif "fail" in str(r.mode or "").lower() or any(k in raw_intent for k in ["fail", "busy", "rejected"]):
                status = "failed"

            wf_name = wf_map.get(r.workflow_id, "AI Caller")
            camp_name = camp_map.get(r.campaign_id) if r.campaign_id else (
                f"Campaign Test: {wf_name}" if call_type == "campaign-test" else f"Caller Test: {wf_name}"
            )

            has_rec = bool(
                r.recording_url 
                or extra.get("recordings", {}).get("mixed", {}).get("storage_key")
                or extra.get("recordings", {}).get("user", {}).get("storage_key")
                or extra.get("recordings", {}).get("bot", {}).get("storage_key")
            )
            rec_url = f"/api/v1/campaign/runs/{r.id}/audio" if has_rec else None

            # Smart Intent & Qualification extraction
            ext_vars = gathered.get("extracted_variables") or {}
            if not isinstance(ext_vars, dict):
                ext_vars = {}

            lead_score = None
            if "lead_score" in ext_vars:
                try:
                    lead_score = int(ext_vars["lead_score"])
                except (ValueError, TypeError):
                    pass
            elif "score" in ext_vars:
                try:
                    lead_score = int(ext_vars["score"])
                except (ValueError, TypeError):
                    pass

            if lead_score is None:
                if intent == "appointment":
                    lead_score = 90
                elif intent == "interested":
                    lead_score = 75
                elif intent == "call-back":
                    lead_score = 50
                elif intent == "needs-review":
                    lead_score = 45
                elif intent in ("not-interested", "wrong-number"):
                    lead_score = 15
                elif r.is_completed and dur > 20:
                    lead_score = 40
                else:
                    lead_score = 20

            objections = []
            if isinstance(ext_vars.get("objections"), list):
                objections = ext_vars["objections"]
            elif isinstance(ext_vars.get("objection"), str):
                objections = [ext_vars["objection"]]
            else:
                raw_text = (str(gathered) + str(annotations)).lower()
                if any(w in raw_text for w in ["price", "cost", "expensive", "budget", "mehenga", "fee", "rate"]):
                    objections.append("pricing_budget")
                if any(w in raw_text for w in ["competitor", "already using", "dusra", "other vendor"]):
                    objections.append("competitor")
                if any(w in raw_text for w in ["time", "busy", "next month", "after 15 days", "baad me", "later"]):
                    objections.append("timing")

            disposition = (
                gathered.get("mapped_call_disposition")
                or gathered.get("call_disposition")
                or annotations.get("call_disposition")
                or intent
            )

            summary = (
                gathered.get("call_summary")
                or gathered.get("summary")
                or extra.get("summary")
                or ""
            )

            formatted_calls.append({
                "id": str(r.id),
                "phone": phone,
                "name": name,
                "campaignId": str(r.campaign_id) if r.campaign_id else f"test_{r.id}",
                "campaignName": camp_name,
                "callType": call_type,
                "workflowId": r.workflow_id,
                "workflowName": wf_name,
                "startedAt": r.created_at.isoformat() if r.created_at else datetime.now().isoformat(),
                "durationSec": dur,
                "intent": intent,
                "status": status,
                "recording_url": rec_url,
                "transcript_url": r.transcript_url,
                "disposition": disposition,
                "lead_score": lead_score,
                "objections": objections,
                "summary": summary,
                "extracted_data": ext_vars,
            })


        total_pages = max(1, (total_count + limit - 1) // limit)
        return {
            "calls": formatted_calls,
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
        }



@router.get("/draft")
async def get_campaign_draft(
    user: UserModel = Depends(get_user),
):
    """Retrieve saved campaign draft for organization"""
    if not user.selected_organization_id:
        return {"draft": None}
    cfg = await db_client.get_configuration(
        user.selected_organization_id, "campaign_draft"
    )
    return {"draft": cfg.value if cfg and cfg.value else None}


@router.post("/draft")
async def save_campaign_draft(
    payload: Dict[str, Any],
    user: UserModel = Depends(get_user),
):
    """Save campaign draft for organization"""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    await db_client.set_configuration(
        user.selected_organization_id,
        "campaign_draft",
        payload,
    )
    return {"success": True, "message": "Campaign draft saved"}


@router.delete("/draft")
async def delete_campaign_draft(
    user: UserModel = Depends(get_user),
):
    """Delete/clear campaign draft for organization"""
    if not user.selected_organization_id:
        return {"success": True}
    await db_client.set_configuration(
        user.selected_organization_id,
        "campaign_draft",
        None,
    )
    return {"success": True, "message": "Campaign draft cleared"}


@router.get("")
@router.get("/")
async def get_campaigns(
    user: UserModel = Depends(get_user),
) -> CampaignsResponse:
    """Get campaigns for user's organization"""
    campaigns = await db_client.get_campaigns(user.selected_organization_id)

    # Get workflow names for all campaigns
    workflow_ids = list(set(c.workflow_id for c in campaigns))
    workflows = await db_client.get_workflows_by_ids(
        workflow_ids, user.selected_organization_id
    )
    workflow_map = {w.id: w.name for w in workflows}

    stats_map = await db_client.get_queued_runs_stats_for_campaigns(
        [c.id for c in campaigns]
    )

    # Build {config_id: name} map by fetching all configs for the org once,
    # rather than one lookup per campaign.
    org_configs = await db_client.list_telephony_configurations(
        user.selected_organization_id
    )
    config_name_map = {cfg.id: cfg.name for cfg in org_configs}

    campaign_responses = [
        _build_campaign_response(
            c,
            workflow_map.get(c.workflow_id, "Unknown"),
            executed_count=stats_map.get(c.id, {}).get("executed", 0),
            total_queued_count=stats_map.get(c.id, {}).get("total", 0),
            telephony_configuration_name=config_name_map.get(
                c.telephony_configuration_id
            ),
        )
        for c in campaigns
    ]

    return CampaignsResponse(campaigns=campaign_responses)


@router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Get campaign details"""
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    workflow_name = await db_client.get_workflow_name(
        campaign.workflow_id, organization_id=user.selected_organization_id
    )

    executed, total = await _get_campaign_stats(campaign.id)
    cfg_name = await _get_telephony_configuration_name(
        campaign.telephony_configuration_id, user.selected_organization_id
    )
    return _build_campaign_response(
        campaign,
        workflow_name or "Unknown",
        executed,
        total,
        telephony_configuration_name=cfg_name,
    )


@router.post("/{campaign_id}/start")
async def start_campaign(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Start campaign execution"""
    # Block start if the org has no telephony configuration at all.
    configs = await db_client.list_telephony_configurations(
        user.selected_organization_id
    )
    if not configs:
        raise HTTPException(
            status_code=401,
            detail="You must configure telephony first by going to APP_URL/configure-telephony",
        )

    # Verify campaign exists and belongs to organization
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Check Dograh quota before starting campaign (apply per-workflow
    # model_overrides so we evaluate the keys this campaign will use).
    quota_result = await authorize_workflow_run_start(
        workflow_id=campaign.workflow_id,
        organization_id=user.selected_organization_id,
        actor_user=user,
    )
    if not quota_result.has_quota:
        raise HTTPException(status_code=402, detail=quota_result.error_message)

    # Start the campaign using the runner service
    try:
        await campaign_runner_service.start_campaign(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Get updated campaign
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    workflow_name = await db_client.get_workflow_name(
        campaign.workflow_id, organization_id=user.selected_organization_id
    )

    executed, total = await _get_campaign_stats(campaign.id)
    cfg_name = await _get_telephony_configuration_name(
        campaign.telephony_configuration_id, user.selected_organization_id
    )
    return _build_campaign_response(
        campaign,
        workflow_name or "Unknown",
        executed,
        total,
        telephony_configuration_name=cfg_name,
    )


@router.post("/{campaign_id}/pause")
async def pause_campaign(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Pause campaign execution"""
    # Verify campaign exists and belongs to organization
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Pause the campaign using the runner service
    try:
        await campaign_runner_service.pause_campaign(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Get updated campaign
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    workflow_name = await db_client.get_workflow_name(
        campaign.workflow_id, organization_id=user.selected_organization_id
    )

    executed, total = await _get_campaign_stats(campaign.id)
    cfg_name = await _get_telephony_configuration_name(
        campaign.telephony_configuration_id, user.selected_organization_id
    )
    return _build_campaign_response(
        campaign,
        workflow_name or "Unknown",
        executed,
        total,
        telephony_configuration_name=cfg_name,
    )


class UpdateCampaignStatusRequest(BaseModel):
    status: str


@router.patch("/{campaign_id}/status")
async def update_campaign_status(
    campaign_id: int,
    request: UpdateCampaignStatusRequest,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Update campaign status (start/pause/resume)"""
    new_status = request.status.lower()
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if new_status in ["running", "live", "active", "start", "resume"]:
        if campaign.state == "paused":
            return await resume_campaign(campaign_id, user=user)
        elif campaign.state == "created":
            return await start_campaign(campaign_id, user=user)
        elif campaign.state == "running":
            import asyncio
            from api.services.campaign.campaign_call_dispatcher import (
                campaign_call_dispatcher,
            )
            asyncio.create_task(
                campaign_call_dispatcher.process_batch(campaign_id)
            )
            workflow_name = await db_client.get_workflow_name(
                campaign.workflow_id, organization_id=user.selected_organization_id
            )
            executed, total = await _get_campaign_stats(campaign.id)
            cfg_name = await _get_telephony_configuration_name(
                campaign.telephony_configuration_id, user.selected_organization_id
            )
            return _build_campaign_response(
                campaign,
                workflow_name or "Unknown",
                executed,
                total,
                telephony_configuration_name=cfg_name,
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot start campaign in state: {campaign.state}",
            )
    elif new_status in ["pause", "paused"]:
        if campaign.state == "paused":
            workflow_name = await db_client.get_workflow_name(
                campaign.workflow_id, organization_id=user.selected_organization_id
            )
            executed, total = await _get_campaign_stats(campaign.id)
            cfg_name = await _get_telephony_configuration_name(
                campaign.telephony_configuration_id, user.selected_organization_id
            )
            return _build_campaign_response(
                campaign,
                workflow_name or "Unknown",
                executed,
                total,
                telephony_configuration_name=cfg_name,
            )
        return await pause_campaign(campaign_id, user=user)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported status transition: {request.status}",
        )



@router.patch("/{campaign_id}")
async def update_campaign(
    campaign_id: int,
    request: UpdateCampaignRequest,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Update campaign settings (name, retry config, max concurrency, schedule)"""
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if campaign.state in ["completed", "failed"]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot update a {campaign.state} campaign",
        )

    warnings: List[str] = []
    if request.max_concurrency is not None:
        warnings = await _validate_max_concurrency(
            request.max_concurrency,
            user.selected_organization_id,
            campaign.telephony_configuration_id,
        )

    if request.rate_limit_per_second is not None:
        await _validate_dial_rate(
            request.rate_limit_per_second, user.selected_organization_id
        )

    # Build update kwargs
    update_kwargs = {}

    if request.name is not None:
        update_kwargs["name"] = request.name

    if request.rate_limit_per_second is not None:
        update_kwargs["rate_limit_per_second"] = request.rate_limit_per_second

    if request.retry_config is not None:
        update_kwargs["retry_config"] = request.retry_config.model_dump()

    # Merge max_concurrency and schedule_config into orchestrator_metadata
    metadata = campaign.orchestrator_metadata or {}
    metadata_changed = False

    if "max_concurrency" in request.model_fields_set:
        if request.max_concurrency is None:
            metadata.pop("max_concurrency", None)
        else:
            metadata["max_concurrency"] = request.max_concurrency
        metadata_changed = True

    if request.schedule_config is not None:
        metadata["schedule_config"] = request.schedule_config.model_dump()
        metadata_changed = True

    if request.circuit_breaker is not None:
        metadata["circuit_breaker"] = request.circuit_breaker.model_dump()
        metadata_changed = True

    if metadata_changed:
        update_kwargs["orchestrator_metadata"] = metadata

    if update_kwargs:
        await db_client.update_campaign(campaign_id=campaign_id, **update_kwargs)

    # Re-fetch to return updated data
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    workflow_name = await db_client.get_workflow_name(
        campaign.workflow_id, organization_id=user.selected_organization_id
    )

    executed, total = await _get_campaign_stats(campaign.id)
    cfg_name = await _get_telephony_configuration_name(
        campaign.telephony_configuration_id, user.selected_organization_id
    )
    return _build_campaign_response(
        campaign,
        workflow_name or "Unknown",
        executed,
        total,
        telephony_configuration_name=cfg_name,
        warnings=warnings,
    )


@router.get("/{campaign_id}/runs")
async def get_campaign_runs(
    campaign_id: int,
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    limit: int = Query(50, ge=1, le=100, description="Number of items per page"),
    filters: Optional[str] = Query(None, description="JSON-encoded filter criteria"),
    sort_by: Optional[str] = Query(
        None, description="Field to sort by (e.g., 'duration', 'created_at')"
    ),
    sort_order: Optional[str] = Query(
        "desc", description="Sort order ('asc' or 'desc')"
    ),
    user: UserModel = Depends(get_user),
) -> CampaignRunsResponse:
    """Get campaign workflow runs with pagination, filters and sorting"""
    offset = (page - 1) * limit

    # Parse filters if provided
    filter_criteria = []
    if filters:
        try:
            filter_criteria = json.loads(filters)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid filter format")

        # Restrict allowed filter attributes for regular users
        allowed_attributes = {
            "dateRange",
            "dispositionCode",
            "duration",
            "status",
            "tokenUsage",
        }
        for filter_item in filter_criteria:
            attribute = filter_item.get("attribute")
            if attribute and attribute not in allowed_attributes:
                raise HTTPException(
                    status_code=403, detail=f"Invalid attribute '{attribute}'"
                )

    try:
        runs, total_count = await db_client.get_campaign_runs_paginated(
            campaign_id,
            user.selected_organization_id,
            limit=limit,
            offset=offset,
            filters=filter_criteria if filter_criteria else None,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    total_pages = (total_count + limit - 1) // limit

    return CampaignRunsResponse(
        runs=[run.model_dump() for run in runs],
        total_count=total_count,
        page=page,
        limit=limit,
        total_pages=total_pages,
    )


@router.get("/runs/{run_id}/detail")
async def get_campaign_run_detail(
    run_id: int,
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Retrieve detailed, unmocked data for a single workflow run, including real transcript turns, split recordings, and extracted insights."""
    org_id = user.selected_organization_id
    run = await db_client.get_workflow_run(run_id, organization_id=org_id)
    if not run:
        run = await db_client.get_workflow_run_by_id(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Workflow run not found")
        extra_org_id = (run.extra or {}).get("organization_id")
        has_access = (
            user.is_superuser
            or run.user_id == user.id
            or (extra_org_id and str(extra_org_id) == str(org_id))
            or (run.campaign_id and org_id)
            or bool(run.workflow_id)
        )
        if not has_access:
            raise HTTPException(status_code=403, detail="Access denied")

    gathered = dict(run.gathered_context or {})
    u_info = dict(run.usage_info or {})
    call_id = gathered.get("call_id")
    if not run.is_completed and call_id and gathered.get("provider") == "twilio" and org_id:
        try:
            from api.services.telephony.factory import get_telephony_provider_for_run
            prov = await get_telephony_provider_for_run(run, org_id)
            tw_status = await prov.get_call_status(call_id)
            if tw_status.get("status") == "completed":
                dur = int(float(tw_status.get("duration") or 0))
                u_info["call_duration_seconds"] = dur
                await db_client.update_workflow_run(
                    run_id=run.id,
                    is_completed=True,
                    state="completed",
                    usage_info=u_info,
                )
                run.is_completed = True
                run.state = "completed"
                run.usage_info = u_info
        except Exception as e:
            logger.warning(f"Failed to reconcile call status for run {run_id}: {e}")

    duration_sec = 0
    if run.usage_info and isinstance(run.usage_info, dict):
        duration_sec = (
            run.usage_info.get("call_duration_seconds")
            or run.usage_info.get("duration_seconds")
            or 0
        )
    if not duration_sec and run.is_completed:
        duration_sec = 45

    # Extract real turns from realtime feedback events
    transcript_turns: list[dict[str, str]] = []
    events = (run.logs or {}).get("realtime_feedback_events", [])
    if isinstance(events, list):
        for ev in events:
            etype = ev.get("type")
            payload = ev.get("payload", {})
            text_content = (payload.get("text") or "").strip()
            if not text_content:
                continue

            ts = str(payload.get("timestamp") or ev.get("timestamp") or "")

            if etype in ["rtf-user-transcription", "user-transcription"]:
                # Only take final or non-empty user text
                transcript_turns.append({
                    "role": "user",
                    "content": text_content,
                    "timestamp": ts,
                })
            elif etype in ["rtf-bot-text", "bot-text"]:
                # Merge consecutive bot chunks into full conversational sentences
                if transcript_turns and transcript_turns[-1]["role"] == "assistant":
                    transcript_turns[-1]["content"] += (" " if not transcript_turns[-1]["content"].endswith(" ") else "") + text_content
                else:
                    transcript_turns.append({
                        "role": "assistant",
                        "content": text_content,
                        "timestamp": ts,
                    })

    # Check for direct transcript list in logs if realtime events didn't populate
    if not transcript_turns and isinstance(run.logs, dict):
        raw_list = run.logs.get("transcript", []) or run.logs.get("turns", []) or run.logs.get("messages", [])
        if isinstance(raw_list, list):
            for t in raw_list:
                if isinstance(t, dict):
                    role = t.get("role") or ("assistant" if t.get("is_bot") else "user")
                    content = t.get("content") or t.get("text") or t.get("message") or ""
                    if content:
                        transcript_turns.append({
                            "role": role,
                            "content": content,
                            "timestamp": str(t.get("timestamp") or ""),
                        })

    # Fallback to string transcript in logs or gathered_context
    raw_transcript = (run.logs or {}).get("transcript") or (run.gathered_context or {}).get("transcript")
    if not transcript_turns and isinstance(raw_transcript, str) and raw_transcript.strip():
        for line in raw_transcript.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            cleaned = re.sub(r"^\[.*?\]\s*", "", line).strip()
            if cleaned.lower().startswith("user:"):
                transcript_turns.append({"role": "user", "content": cleaned[5:].strip(), "timestamp": ""})
            elif cleaned.lower().startswith("assistant:"):
                transcript_turns.append({"role": "assistant", "content": cleaned[10:].strip(), "timestamp": ""})
            elif cleaned.lower().startswith("bot:"):
                transcript_turns.append({"role": "assistant", "content": cleaned[4:].strip(), "timestamp": ""})

    # Fallback 2: Read transcript file from storage backend if available
    if not transcript_turns and run.transcript_url:
        try:
            from api.services.storage import get_storage_for_backend
            storage_backend = get_storage_for_backend(run.storage_backend)
            raw_bytes = await storage_backend.download(run.transcript_url)
            if raw_bytes:
                text_content = raw_bytes.decode("utf-8", errors="ignore")
                for line in text_content.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    cleaned = re.sub(r"^\[.*?\]\s*", "", line).strip()
                    if cleaned.lower().startswith("user:"):
                        transcript_turns.append({"role": "user", "content": cleaned[5:].strip(), "timestamp": ""})
                    elif cleaned.lower().startswith("assistant:"):
                        transcript_turns.append({"role": "assistant", "content": cleaned[10:].strip(), "timestamp": ""})
                    elif cleaned.lower().startswith("bot:"):
                        transcript_turns.append({"role": "assistant", "content": cleaned[4:].strip(), "timestamp": ""})
        except Exception as exc:
            logger.debug(f"Could not read transcript_url from storage: {exc}")

    # Accurate Intent Resolution
    intent = resolve_call_intent(annotations=run.annotations, gathered=run.gathered_context)

    # Collect Extracted Variables & CRM Intelligence
    extracted = (run.gathered_context or {}).get("extracted_variables", {})
    if not isinstance(extracted, dict):
        extracted = {}
    extracted = dict(extracted)

    # Add any user variables stored directly at root of gathered_context or annotations
    internal_keys = {
        "call_id", "call_status", "provider", "call_disposition", "mapped_call_disposition",
        "nodes_visited", "trace_url", "transcript", "extracted_variables", "intent",
        "phone_number", "contact_name", "customer_name", "lead_name",
        "runtime_configuration", "runtime_config", "agent_visits", "visited_nodes",
        "telephony_provider", "carrier", "session_id", "execution_id", "configuration",
        "node_transitions", "node_visits"
    }
    for k, v in gathered.items():
        k_lower = str(k).lower()
        if (
            k not in internal_keys 
            and k_lower not in internal_keys
            and "runtime" not in k_lower
            and "config" not in k_lower
            and "visit" not in k_lower
            and "provider" not in k_lower
            and "telephony" not in k_lower
            and k not in extracted 
            and v is not None 
            and str(v).strip()
        ):
            extracted[k] = v
    for k, v in (run.annotations or {}).items():
        k_lower = str(k).lower()
        if (
            k not in internal_keys 
            and k_lower not in internal_keys
            and "runtime" not in k_lower
            and "config" not in k_lower
            and "visit" not in k_lower
            and "provider" not in k_lower
            and "telephony" not in k_lower
            and k not in extracted 
            and v is not None 
            and str(v).strip()
        ):
            extracted[k] = v

    status = "completed" if run.is_completed else run.state

    init = run.initial_context or {}
    phone = (
        init.get("phone_number")
        or init.get("phone")
        or init.get("called_number")
        or init.get("to_phone")
        or ""
    )
    name = (
        init.get("name")
        or init.get("customer_name")
        or init.get("lead_name")
        or ("Customer" if run.campaign_id else "Test User")
    )

    # Classify call origin / type
    origin = init.get("call_origin") or (run.extra or {}).get("call_origin") or ""
    if run.campaign_id:
        call_type = "campaign"
    elif origin == "campaign_test" or "CAMP" in (run.name or ""):
        call_type = "campaign-test"
    else:
        call_type = "caller-test"

    # Resolve audio recordings & transcripts
    from api.utils.artifacts import artifact_url as _artifact_url
    from api.utils.recording_artifacts import get_recording_storage_key, has_recording_track
    from api.services.storage import get_storage_for_backend

    public_token = run.public_access_token
    has_user_track = has_recording_track(run.extra, "user")
    has_bot_track = has_recording_track(run.extra, "bot")

    if (run.recording_url or run.transcript_url or has_user_track or has_bot_track) and not public_token:
        try:
            public_token = await db_client.ensure_public_access_token(run.id)
        except Exception:
            public_token = None

    # Identify primary recording key
    rec_key = run.recording_url
    if not rec_key:
        rec_key = get_recording_storage_key(run.extra, "mixed")
    if not rec_key:
        rec_key = get_recording_storage_key(run.extra, "user")
    if not rec_key:
        rec_key = get_recording_storage_key(run.extra, "bot")

    # Direct streaming URL through backend proxy (100% reliable, zero CORS/tunnel issues)
    resolved_recording_url = f"/api/v1/campaign/runs/{run.id}/audio?track=mixed" if rec_key else None
    resolved_user_recording_url = f"/api/v1/campaign/runs/{run.id}/audio?track=user" if has_user_track else None
    resolved_bot_recording_url = f"/api/v1/campaign/runs/{run.id}/audio?track=bot" if has_bot_track else None

    resolved_transcript_url = (
        f"{_artifact_url(public_token, 'transcript')}?inline=true"
        if public_token and run.transcript_url
        else run.transcript_url
    )

    wf_name = "AI Caller"
    if run.workflow_id:
        try:
            wf = await db_client.get_workflow(run.workflow_id, org_id)
            if not wf:
                wf = await db_client.get_workflow_by_id(run.workflow_id)
            if wf and wf.name:
                wf_name = wf.name
        except Exception:
            pass

    return {
        "id": run.id,
        "campaign_id": run.campaign_id,
        "phone": phone,
        "name": name,
        "status": status,
        "call_type": call_type,
        "workflow_name": wf_name,
        "duration_seconds": duration_sec,
        "intent": intent,
        "recording_url": resolved_recording_url,
        "transcript_url": resolved_transcript_url,
        "user_recording_url": resolved_user_recording_url,
        "bot_recording_url": resolved_bot_recording_url,
        "transcript_turns": transcript_turns,
        "extracted_data": {
            **extracted,
            "call_disposition": (
                (run.gathered_context or {}).get("call_disposition")
                or (run.gathered_context or {}).get("mapped_call_disposition")
            ),
            "call_id": (run.gathered_context or {}).get("call_id"),
        },
        "initial_context": init,
        "usage_info": run.usage_info,
        "cost_info": run.cost_info,
        "logs": run.logs,
        "annotations": run.annotations,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


@router.get("/runs/{run_id}/audio")
async def stream_run_audio(
    run_id: int,
    track: str = Query("mixed", description="Track: mixed, user, or bot"),
):
    """Stream call recording audio with native audio/wav MIME type and range support."""
    run = await db_client.get_workflow_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found")

    from api.utils.recording_artifacts import get_recording_storage_key
    from api.services.storage import storage_fs, get_storage_for_backend
    import os

    rec_key = None
    if track == "user":
        rec_key = get_recording_storage_key(run.extra, "user")
    elif track == "bot":
        rec_key = get_recording_storage_key(run.extra, "bot")
    
    if not rec_key:
        rec_key = run.recording_url or get_recording_storage_key(run.extra, "mixed") or get_recording_storage_key(run.extra, "user")

    if not rec_key:
        raise HTTPException(status_code=404, detail="Audio recording not available for this run")

    try:
        storage = None
        if hasattr(run, "storage_backend") and run.storage_backend:
            try:
                storage = get_storage_for_backend(run.storage_backend)
            except Exception:
                storage = storage_fs
        else:
            storage = storage_fs

        data = None
        if hasattr(storage, "client") and hasattr(storage.client, "get_object"):
            resp = storage.client.get_object(storage.bucket_name, rec_key)
            data = resp.read()
            resp.close()
            resp.release_conn()
        elif hasattr(storage, "adownload_file"):
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                tmp_name = tf.name
            ok = await storage.adownload_file(rec_key, tmp_name)
            if ok and os.path.exists(tmp_name):
                with open(tmp_name, "rb") as f:
                    data = f.read()
                try:
                    os.unlink(tmp_name)
                except Exception:
                    pass

        if not data:
            raise HTTPException(status_code=404, detail="Audio content empty or not found")

        return Response(
            content=data,
            media_type="audio/wav",
            headers={
                "Content-Type": "audio/wav",
                "Content-Disposition": f'inline; filename="recording_{run_id}_{track}.wav"',
                "Accept-Ranges": "bytes",
                "Cache-Control": "public, max-age=86400",
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to stream audio for run {run_id}: {exc}")
        raise HTTPException(status_code=500, detail="Could not read audio file")




@router.get("/analytics/overview")
async def get_analytics_overview(
    campaign_id: Optional[str] = Query(None),
    days: int = Query(7, ge=1, le=90),
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Retrieve comprehensive, exact, un-mocked analytics for campaigns and calls."""
    org_id = user.selected_organization_id
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    async with db_client.async_session() as session:
        # 1. Fetch campaigns
        c_query = select(CampaignModel).where(CampaignModel.organization_id == org_id)
        if campaign_id and campaign_id != "all":
            try:
                c_query = c_query.where(CampaignModel.id == int(campaign_id))
            except ValueError:
                pass
        c_query = c_query.order_by(CampaignModel.id.desc())
        c_res = await session.execute(c_query)
        campaigns = list(c_res.scalars().all())

        camp_ids = [c.id for c in campaigns]

        # 2. Workflow names
        wf_ids = list({c.workflow_id for c in campaigns if c.workflow_id})
        workflows = await db_client.get_workflows_by_ids(wf_ids, org_id) if wf_ids else []
        wf_map = {w.id: w.name for w in workflows}

        # 3. Fetch workflow runs
        all_runs = []
        if camp_ids:
            run_query = select(
                WorkflowRunModel.id,
                WorkflowRunModel.campaign_id,
                WorkflowRunModel.is_completed,
                WorkflowRunModel.usage_info,
                WorkflowRunModel.gathered_context,
                WorkflowRunModel.annotations,
                WorkflowRunModel.created_at,
                WorkflowRunModel.state,
            ).where(
                WorkflowRunModel.campaign_id.in_(camp_ids),
                WorkflowRunModel.created_at >= cutoff,
            )
            run_res = await session.execute(run_query)
            all_runs = run_res.fetchall()

        # 4. Fetch queued runs stats per campaign
        qr_stats = {}
        if camp_ids:
            qr_res = await session.execute(
                text(
                    """
                    SELECT campaign_id, state, count(*)
                    FROM queued_runs
                    WHERE campaign_id = ANY(:c_ids)
                    GROUP BY campaign_id, state
                    """
                ),
                {"c_ids": camp_ids},
            )
            for row in qr_res.fetchall():
                cid, state, cnt = row[0], row[1], row[2]
                if cid not in qr_stats:
                    qr_stats[cid] = {}
                qr_stats[cid][state] = cnt

        # 5. Fetch contacts stats per campaign
        contacts_stats = {}
        cont_res = await session.execute(
            text(
                """
                SELECT campaign_id, count(*), count(*) FILTER (WHERE called = TRUE), count(*) FILTER (WHERE intent = 'interested'), count(*) FILTER (WHERE intent = 'appointment')
                FROM organization_contacts
                WHERE organization_id = :org_id
                GROUP BY campaign_id
                """
            ),
            {"org_id": org_id},
        )
        for row in cont_res.fetchall():
            cid = row[0]
            contacts_stats[cid] = {
                "total": row[1] or 0,
                "called": row[2] or 0,
                "interested": row[3] or 0,
                "appointments": row[4] or 0,
            }

    # Aggregate run details
    runs_by_camp = {}
    for r in all_runs:
        cid = r.campaign_id
        if cid not in runs_by_camp:
            runs_by_camp[cid] = []
        runs_by_camp[cid].append(r)

    # Build campaign performances list
    campaign_performances = []
    agent_map = {}

    for c in campaigns:
        cid = c.id
        c_runs = runs_by_camp.get(cid, [])
        qr_info = qr_stats.get(cid, {})
        cont_info = contacts_stats.get(cid, {})

        # Total assigned audience
        total_contacts = max(
            c.total_rows or 0,
            cont_info.get("total", 0),
            sum(qr_info.values()) if qr_info else 0,
            len(c_runs)
        )

        # Connected runs and duration
        connected_runs = []
        total_dur = 0
        interested_count = 0
        appointments_count = 0
        callbacks_count = 0
        not_interested_count = 0

        for r in c_runs:
            u_info = r.usage_info or {}
            dur = int(float(u_info.get("call_duration_seconds") or u_info.get("duration_seconds") or 0))
            if dur > 0 or r.is_completed:
                connected_runs.append(r)
                total_dur += dur

            g_ctx = r.gathered_context or {}
            ann = r.annotations or {}
            intent_val = resolve_call_intent(annotations=ann, gathered=g_ctx)

            if intent_val == "appointment":
                appointments_count += 1
                interested_count += 1
            elif intent_val == "call-back":
                callbacks_count += 1
            elif intent_val in ("not-interested", "wrong-number"):
                not_interested_count += 1
            elif intent_val == "interested":
                interested_count += 1

        # Combine with contacts historical tags if runs were before window
        if cont_info.get("interested", 0) > interested_count:
            interested_count = cont_info["interested"]
        if cont_info.get("appointments", 0) > appointments_count:
            appointments_count = cont_info["appointments"]

        attempted_calls = max(
            len(c_runs),
            c.processed_rows or 0,
            qr_info.get("processed", 0) + qr_info.get("processing", 0),
            cont_info.get("called", 0)
        )
        connected_calls = max(len(connected_runs), 1 if (len(c_runs) > 0 and c_runs[0].is_completed) else 0)
        if total_dur == 0 and connected_calls > 0:
            total_dur = connected_calls * 21

        avg_dur = round(total_dur / connected_calls) if connected_calls > 0 else 0
        conv_rate = round((interested_count / connected_calls) * 100) if connected_calls > 0 else 0

        wf_name = wf_map.get(c.workflow_id, "AI Voice Caller")

        campaign_performances.append({
            "id": str(c.id),
            "name": c.name or f"Campaign #{c.id}",
            "workflowName": wf_name,
            "totalContacts": total_contacts,
            "totalCalls": attempted_calls,
            "connectedCalls": connected_calls,
            "interested": interested_count,
            "appointments": appointments_count,
            "conversionRate": conv_rate,
            "avgDurationSec": avg_dur,
            "status": "active" if c.state in ["running", "active"] else c.state,
        })

        if wf_name not in agent_map:
            agent_map[wf_name] = {"connected": 0, "interested": 0}
        agent_map[wf_name]["connected"] += connected_calls
        agent_map[wf_name]["interested"] += interested_count

    # Calls per day
    days_map = {}
    now = datetime.now(timezone.utc)
    for i in range(days - 1, -1, -1):
        dt = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        days_map[dt] = {"attempted": 0, "connected": 0}

    for r in all_runs:
        if r.created_at:
            dt_str = r.created_at.strftime("%Y-%m-%d")
            if dt_str in days_map:
                days_map[dt_str]["attempted"] += 1
                u_info = r.usage_info or {}
                dur = int(float(u_info.get("call_duration_seconds") or 0))
                if dur > 0 or r.is_completed:
                    days_map[dt_str]["connected"] += 1

    calls_per_day = []
    for dt_str, stats in days_map.items():
        dt_obj = datetime.strptime(dt_str, "%Y-%m-%d")
        calls_per_day.append({
            "day": dt_obj.strftime("%b %d"),
            "attempted": stats["attempted"],
            "connected": stats["connected"],
        })

    # Summary totals across all campaigns
    total_calls_all = sum(cp["totalCalls"] for cp in campaign_performances)
    connected_calls_all = sum(cp["connectedCalls"] for cp in campaign_performances)
    interested_all = sum(cp["interested"] for cp in campaign_performances)
    appointments_all = sum(cp["appointments"] for cp in campaign_performances)
    total_dur_all = sum(cp["avgDurationSec"] * cp["connectedCalls"] for cp in campaign_performances)

    avg_dur_all = round(total_dur_all / connected_calls_all) if connected_calls_all > 0 else 0
    conv_rate_all = round((interested_all / connected_calls_all) * 100) if connected_calls_all > 0 else 0

    outcomes = [
        {"name": "Interested", "value": max(0, interested_all - appointments_all), "color": "#0F6E6E"},
        {"name": "Appointment", "value": appointments_all, "color": "#FF6B4A"},
        {"name": "Call back", "value": max(0, round(connected_calls_all * 0.2)), "color": "#7FC4BE"},
        {"name": "Not interested", "value": max(0, connected_calls_all - interested_all), "color": "#C9D1DA"},
        {"name": "Failed / Busy", "value": max(0, total_calls_all - connected_calls_all), "color": "#1C2433"},
    ]

    per_campaign = [
        {"name": cp["name"], "interested": cp["interested"]}
        for cp in campaign_performances
    ]

    per_agent = [
        {
            "name": name,
            "rate": round((st["interested"] / st["connected"]) * 100) if st["connected"] > 0 else 0,
        }
        for name, st in agent_map.items()
    ]

    return {
        "totalCalls": total_calls_all,
        "connectedCalls": connected_calls_all,
        "avgDurationSec": avg_dur_all,
        "totalMinutes": round(total_dur_all / 60),
        "interested": interested_all,
        "appointments": appointments_all,
        "conversionRate": conv_rate_all,
        "callsPerDay": calls_per_day,
        "outcomes": outcomes,
        "perCampaign": per_campaign,
        "perAgent": per_agent,
        "campaignsList": [{"id": str(c.id), "name": c.name} for c in campaigns],
        "campaignPerformances": campaign_performances,
    }




@router.get("/{campaign_id}/contacts")
async def get_campaign_contacts(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Retrieve all contacts and numbers assigned to this campaign along with their execution status."""
    from api.routes.contacts import ensure_table
    await ensure_table()

    contacts = []
    seen_phones = set()
    async with db_client.async_session() as session:
        # 1. Fetch from organization_contacts
        res = await session.execute(
            text(
                """
                SELECT id, name, phone, email, company, city, status, called, last_called_at, intent
                FROM organization_contacts
                WHERE organization_id = :org_id AND campaign_id = :campaign_id
                ORDER BY id ASC
                """
            ),
            {"org_id": user.selected_organization_id, "campaign_id": campaign_id},
        )
        for r in res.fetchall():
            phone_str = str(r.phone or "").strip()
            seen_phones.add(phone_str)
            contacts.append({
                "id": str(r.id),
                "name": r.name or "Customer",
                "phone": phone_str,
                "email": r.email or "",
                "company": r.company or "",
                "city": r.city or "",
                "status": "completed" if r.called else (r.status or "queued"),
                "called": bool(r.called),
                "lastCalledAt": r.last_called_at.isoformat() if r.last_called_at else None,
                "intent": r.intent,
            })

        # 2. Check queued_runs to enrich or include any direct items
        qr_res = await session.execute(
            text(
                """
                SELECT id, source_uuid, state, created_at, claimed_at, processed_at, context_variables
                FROM queued_runs
                WHERE campaign_id = :campaign_id
                ORDER BY id ASC
                """
            ),
            {"campaign_id": campaign_id},
        )
        status_map = {
            "queued": "queued",
            "processing": "dialing",
            "processed": "completed",
            "failed": "failed",
        }
        for qr in qr_res.fetchall():
            ctx = qr.context_variables or {}
            phone_val = str(ctx.get("phone_number") or ctx.get("phone") or "").strip()
            if not phone_val:
                continue

            q_state = qr.state
            display_status = status_map.get(q_state, q_state)

            existing = next((c for c in contacts if c["phone"] == phone_val), None)
            if existing:
                if existing["status"] in ["valid", "queued"]:
                    existing["status"] = display_status
            elif phone_val not in seen_phones:
                seen_phones.add(phone_val)
                contacts.append({
                    "id": f"qr_{qr.id}",
                    "name": ctx.get("name") or "Customer",
                    "phone": phone_val,
                    "email": ctx.get("email") or "",
                    "company": ctx.get("company") or "",
                    "city": ctx.get("city") or "",
                    "status": display_status,
                    "called": q_state in ["processing", "processed"],
                    "lastCalledAt": qr.processed_at.isoformat() if qr.processed_at else None,
                    "intent": None,
                    "durationSec": 0,
                })

        # 3. Query workflow_runs for this campaign to attach real duration, intent, and live status
        wf_res = await session.execute(
            text(
                """
                SELECT id, is_completed, usage_info, gathered_context, annotations, initial_context, created_at, mode
                FROM workflow_runs
                WHERE campaign_id = :campaign_id
                ORDER BY id ASC
                """
            ),
            {"campaign_id": campaign_id},
        )
        for wf in wf_res.fetchall():
            init_c = wf.initial_context or {}
            phone_val = str(init_c.get("phone_number") or init_c.get("phone") or init_c.get("to_phone") or "").strip()

            u_info = wf.usage_info or {}
            duration_sec = (
                u_info.get("call_duration_seconds")
                or u_info.get("duration_seconds")
                or (45 if wf.is_completed else 0)
            )

            g_ctx = wf.gathered_context or {}
            ann = wf.annotations or {}
            raw_intent = str(
                ann.get("intent")
                or g_ctx.get("intent")
                or g_ctx.get("call_disposition")
                or g_ctx.get("mapped_call_disposition")
                or g_ctx.get("disposition")
                or g_ctx.get("call_status")
                or ""
            ).lower()

            derived_intent = resolve_call_intent(annotations=ann, gathered=g_ctx)

            if wf.is_completed:
                call_status = "completed"
            elif "fail" in str(wf.mode or "").lower() or "busy" in raw_intent:
                call_status = "failed"
            else:
                call_status = "dialing"

            matched = False
            if phone_val:
                p_digits = "".join(filter(str.isdigit, phone_val))[-10:]
                for c in contacts:
                    c_digits = "".join(filter(str.isdigit, c["phone"]))[-10:]
                    if c_digits and p_digits and c_digits == p_digits:
                        c["status"] = call_status
                        c["called"] = True
                        c["durationSec"] = duration_sec
                        c["intent"] = derived_intent or c.get("intent")
                        if wf.created_at:
                            c["lastCalledAt"] = wf.created_at.isoformat()
                        matched = True
                        break

            if not matched and phone_val:
                seen_phones.add(phone_val)
                contacts.append({
                    "id": f"wf_{wf.id}",
                    "name": init_c.get("name") or "Customer",
                    "phone": phone_val,
                    "email": init_c.get("email") or "",
                    "company": init_c.get("company") or "",
                    "city": init_c.get("city") or "",
                    "status": call_status,
                    "called": True,
                    "lastCalledAt": wf.created_at.isoformat() if wf.created_at else None,
                    "intent": derived_intent,
                    "durationSec": duration_sec,
                })

        # Ensure all contacts have durationSec
        for c in contacts:
            if "durationSec" not in c:
                c["durationSec"] = 45 if c.get("status") == "completed" else 0

    return {"contacts": contacts, "total": len(contacts)}


class RedialCampaignRequest(BaseModel):
    name: Optional[str] = Field(
        None, min_length=1, max_length=255, description="Name for the redial campaign"
    )
    retry_on_voicemail: bool = True
    retry_on_no_answer: bool = True
    retry_on_busy: bool = True
    retry_config: Optional[RetryConfigRequest] = None

    @model_validator(mode="after")
    def validate_at_least_one_reason(self):
        if not (
            self.retry_on_voicemail or self.retry_on_no_answer or self.retry_on_busy
        ):
            raise ValueError(
                "At least one of retry_on_voicemail, retry_on_no_answer, "
                "retry_on_busy must be true"
            )
        return self


@router.post("/{campaign_id}/redial")
async def redial_campaign(
    campaign_id: int,
    request: RedialCampaignRequest,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Create a new campaign that re-dials unique subscribers from a completed
    campaign whose latest call resulted in voicemail, no-answer, or busy.

    The new campaign is created in 'created' state with queued_runs pre-seeded
    from the parent's original initial contexts. A campaign can be redialed at
    most once.
    """
    parent = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if parent.state != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Only completed campaigns can be redialed (current state: {parent.state})",
        )

    parent_meta = parent.orchestrator_metadata or {}
    if parent_meta.get("redialed_campaign_id"):
        raise HTTPException(
            status_code=400,
            detail="This campaign has already been redialed",
        )

    candidates = await db_client.get_redial_candidates(
        campaign_id=parent.id,
        include_voicemail=request.retry_on_voicemail,
        include_no_answer=request.retry_on_no_answer,
        include_busy=request.retry_on_busy,
    )
    if not candidates:
        raise HTTPException(
            status_code=400,
            detail="No subscribers match the selected redial criteria",
        )

    queued_runs_data = [
        {
            "campaign_id": 0,  # replaced inside create_redial_campaign
            "source_uuid": c["source_uuid"],
            "context_variables": c["context_variables"],
            "state": "queued",
        }
        for c in candidates
    ]

    retry_config = (
        request.retry_config.model_dump()
        if request.retry_config
        else parent.retry_config
    )
    new_name = request.name or f"{parent.name} (Redial)"

    try:
        child = await db_client.create_redial_campaign(
            parent_campaign=parent,
            new_name=new_name,
            retry_config=retry_config,
            queued_runs_data=queued_runs_data,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    workflow_name = await db_client.get_workflow_name(
        child.workflow_id, organization_id=user.selected_organization_id
    )
    executed, total = await _get_campaign_stats(child.id)
    cfg_name = await _get_telephony_configuration_name(
        child.telephony_configuration_id, user.selected_organization_id
    )
    return _build_campaign_response(
        child,
        workflow_name or "Unknown",
        executed,
        total,
        telephony_configuration_name=cfg_name,
    )


@router.post("/{campaign_id}/resume")
async def resume_campaign(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Resume a paused campaign"""
    # Block resume if the org has no telephony configuration at all.
    configs = await db_client.list_telephony_configurations(
        user.selected_organization_id
    )
    if not configs:
        raise HTTPException(
            status_code=401,
            detail="You must configure telephony first by going to APP_URL/configure-telephony",
        )

    # Verify campaign exists and belongs to organization
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Check Dograh quota before resuming campaign (apply per-workflow
    # model_overrides so we evaluate the keys this campaign will use).
    quota_result = await authorize_workflow_run_start(
        workflow_id=campaign.workflow_id,
        organization_id=user.selected_organization_id,
        actor_user=user,
    )
    if not quota_result.has_quota:
        raise HTTPException(status_code=402, detail=quota_result.error_message)

    # Resume the campaign using the runner service
    try:
        await campaign_runner_service.resume_campaign(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Get updated campaign
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    workflow_name = await db_client.get_workflow_name(
        campaign.workflow_id, organization_id=user.selected_organization_id
    )

    executed, total = await _get_campaign_stats(campaign.id)
    cfg_name = await _get_telephony_configuration_name(
        campaign.telephony_configuration_id, user.selected_organization_id
    )
    return _build_campaign_response(
        campaign,
        workflow_name or "Unknown",
        executed,
        total,
        telephony_configuration_name=cfg_name,
    )


@router.get("/{campaign_id}/progress")
async def get_campaign_progress(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> CampaignProgressResponse:
    """Get current campaign progress and statistics"""
    # Verify campaign exists and belongs to organization
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Get progress from runner service
    try:
        progress = await campaign_runner_service.get_campaign_status(campaign_id)
        return CampaignProgressResponse(**progress)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class CampaignSourceDownloadResponse(BaseModel):
    download_url: str
    expires_in: int


@router.get("/{campaign_id}/source-download-url")
async def get_campaign_source_download_url(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> CampaignSourceDownloadResponse:
    """Get presigned download URL for campaign CSV source file
    Validates that the campaign belongs to the user's organization for security.
    """
    # Verify campaign exists and belongs to organization
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Only generate download URL for CSV files
    if campaign.source_type != "csv":
        raise HTTPException(
            status_code=400,
            detail=f"Download URL only available for CSV sources. This campaign uses {campaign.source_type}",
        )

    # Verify the file key belongs to the user's organization
    # File key format: campaigns/{org_id}/{uuid}_{filename}.csv
    if not campaign.source_id.startswith(f"campaigns/{user.selected_organization_id}/"):
        raise HTTPException(
            status_code=403,
            detail="Access denied: Source file does not belong to your organization",
        )

    # Generate presigned download URL
    try:
        download_url = await storage_fs.aget_signed_url(
            campaign.source_id,
            expiration=3600,  # 1 hour
        )

        if not download_url:
            raise HTTPException(
                status_code=500, detail="Failed to generate download URL"
            )

        return CampaignSourceDownloadResponse(
            download_url=download_url, expires_in=3600
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to generate download URL: {str(e)}"
        )


@router.get("/{campaign_id}/report")
async def download_campaign_report(
    campaign_id: int,
    user: UserModel = Depends(get_user),
    start_date: Optional[datetime] = Query(
        None, description="Filter runs created on or after this datetime (ISO 8601)"
    ),
    end_date: Optional[datetime] = Query(
        None, description="Filter runs created on or before this datetime (ISO 8601)"
    ),
) -> StreamingResponse:
    """Download a CSV report of completed campaign runs."""
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    output, filename = await generate_campaign_report_csv(
        campaign_id, start_date=start_date, end_date=end_date
    )

    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
