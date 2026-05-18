import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from api.constants import (
    DEFAULT_CAMPAIGN_RETRY_CONFIG,
    DEFAULT_ORG_CONCURRENCY_LIMIT,
)
from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationConfigurationKey
from api.services.auth.depends import get_user
from api.services.campaign.runner import campaign_runner_service
from api.services.campaign.source_sync import CampaignSourceSyncService
from api.services.campaign.source_sync_factory import get_sync_service
from api.services.quota_service import check_dograh_quota
from api.services.reports import generate_campaign_report_csv
from api.services.storage import storage_fs

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


async def _get_from_numbers_count(organization_id: int) -> int:
    """Active phone-number count from the org's default telephony config.
    Used to validate ``max_concurrency`` against caller-id supply."""
    try:
        default_cfg = await db_client.get_default_telephony_configuration(
            organization_id
        )
        if default_cfg:
            addresses = await db_client.list_active_normalized_addresses_for_config(
                default_cfg.id
            )
            return len(addresses)
    except Exception:
        pass
    return 0


async def _validate_max_concurrency(max_concurrency: int, organization_id: int) -> None:
    """Validate max_concurrency against org limit and configured phone numbers.

    Raises HTTPException(400) if the value exceeds the effective limit.
    """
    org_limit = await _get_org_concurrent_limit(organization_id)
    from_numbers_count = await _get_from_numbers_count(organization_id)
    effective_limit = (
        min(org_limit, from_numbers_count) if from_numbers_count > 0 else org_limit
    )
    if max_concurrency > effective_limit:
        if from_numbers_count > 0 and from_numbers_count < org_limit:
            raise HTTPException(
                status_code=400,
                detail=f"max_concurrency ({max_concurrency}) cannot exceed {effective_limit}. You have {from_numbers_count} phone number(s) configured. Add more CLIs in telephony configuration to increase concurrency.",
            )
        raise HTTPException(
            status_code=400,
            detail=f"max_concurrency ({max_concurrency}) cannot exceed organization limit ({effective_limit})",
        )


class RetryConfigRequest(BaseModel):
    enabled: bool = True
    max_retries: int = Field(default=2, ge=0, le=10)
    retry_delay_seconds: int = Field(default=120, ge=30, le=3600)
    retry_on_busy: bool = True
    retry_on_no_answer: bool = True
    retry_on_voicemail: bool = True


class RetryConfigResponse(BaseModel):
    enabled: bool
    max_retries: int
    retry_delay_seconds: int
    retry_on_busy: bool
    retry_on_no_answer: bool
    retry_on_voicemail: bool


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
    workflow_id: int
    source_type: str = Field(..., pattern="^(google-sheet|csv)$")
    source_id: str  # Google Sheet URL or CSV file key
    # Optional during the legacy → multi-config migration window. Required in
    # a follow-up. When omitted, the dispatcher falls back to the org's
    # default config.
    telephony_configuration_id: Optional[int] = None
    retry_config: Optional[RetryConfigRequest] = None
    max_concurrency: Optional[int] = Field(default=None, ge=1, le=100)
    schedule_config: Optional[ScheduleConfigRequest] = None
    circuit_breaker: Optional[CircuitBreakerConfigRequest] = None


class UpdateCampaignRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    retry_config: Optional[RetryConfigRequest] = None
    max_concurrency: Optional[int] = Field(default=None, ge=1, le=100)
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
    schedule_config: Optional[ScheduleConfigResponse] = None
    circuit_breaker: Optional[CircuitBreakerConfigResponse] = None
    executed_count: int = 0
    total_queued_count: int = 0
    parent_campaign_id: Optional[int] = None
    redialed_campaign_id: Optional[int] = None
    telephony_configuration_id: Optional[int] = None
    telephony_configuration_name: Optional[str] = None
    logs: List[CampaignLogEntryResponse] = Field(default_factory=list)


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
) -> CampaignResponse:
    """Build a CampaignResponse from a campaign model."""
    # Get retry_config from campaign or use defaults
    retry_config = (
        campaign.retry_config
        if campaign.retry_config
        else DEFAULT_CAMPAIGN_RETRY_CONFIG
    )

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
        schedule_config=schedule_config,
        circuit_breaker=circuit_breaker_config,
        executed_count=executed_count,
        total_queued_count=total_queued_count,
        parent_campaign_id=parent_campaign_id,
        redialed_campaign_id=redialed_campaign_id,
        telephony_configuration_id=campaign.telephony_configuration_id,
        telephony_configuration_name=telephony_configuration_name,
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
        config_id, organization_id
    )
    return cfg.name if cfg else None


@router.post("/create")
async def create_campaign(
    request: CreateCampaignRequest,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Create a new campaign"""
    # Verify workflow exists and belongs to organization
    workflow_name = await db_client.get_workflow_name(request.workflow_id, user.id)
    if not workflow_name:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Validate source data (phone_number column and format)
    sync_service = get_sync_service(request.source_type)
    validation_result = await sync_service.validate_source(
        request.source_id, user.selected_organization_id
    )
    if not validation_result.is_valid:
        raise HTTPException(status_code=400, detail=validation_result.error.message)

    # Validate template variables against source data columns
    workflow = await db_client.get_workflow(
        request.workflow_id, organization_id=user.selected_organization_id
    )
    if workflow:
        from api.services.workflow.dto import ReactFlowDTO
        from api.services.workflow.workflow_graph import WorkflowGraph

        workflow_def = workflow.released_definition.workflow_json
        if workflow_def:
            try:
                dto = ReactFlowDTO(**workflow_def)
                graph = WorkflowGraph(dto)
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
                pass  # Don't block campaign creation if template extraction fails

    if request.max_concurrency is not None:
        await _validate_max_concurrency(
            request.max_concurrency, user.selected_organization_id
        )

    # Resolve which telephony config the campaign is pinned to. Explicit value
    # wins; otherwise default to the org's default config so legacy clients keep
    # working through the migration window.
    telephony_configuration_id = request.telephony_configuration_id
    if telephony_configuration_id:
        cfg = await db_client.get_telephony_configuration_for_org(
            telephony_configuration_id, user.selected_organization_id
        )
        if not cfg:
            raise HTTPException(
                status_code=400, detail="telephony_configuration_not_found"
            )
    else:
        default_cfg = await db_client.get_default_telephony_configuration(
            user.selected_organization_id
        )
        if default_cfg:
            telephony_configuration_id = default_cfg.id

    # Build retry_config dict if provided
    retry_config = None
    if request.retry_config:
        retry_config = request.retry_config.model_dump()

    # Build schedule_config dict if provided
    schedule_config = None
    if request.schedule_config:
        schedule_config = request.schedule_config.model_dump()

    # Build circuit_breaker dict if provided
    circuit_breaker_config = None
    if request.circuit_breaker:
        circuit_breaker_config = request.circuit_breaker.model_dump()

    campaign = await db_client.create_campaign(
        name=request.name,
        workflow_id=request.workflow_id,
        source_type=request.source_type,
        source_id=request.source_id,
        user_id=user.id,
        organization_id=user.selected_organization_id,
        retry_config=retry_config,
        max_concurrency=request.max_concurrency,
        schedule_config=schedule_config,
        circuit_breaker=circuit_breaker_config,
        telephony_configuration_id=telephony_configuration_id,
    )

    cfg_name = await _get_telephony_configuration_name(
        campaign.telephony_configuration_id, user.selected_organization_id
    )
    return _build_campaign_response(
        campaign, workflow_name, telephony_configuration_name=cfg_name
    )


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

    workflow_name = await db_client.get_workflow_name(campaign.workflow_id, user.id)

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
    quota_result = await check_dograh_quota(user, workflow_id=campaign.workflow_id)
    if not quota_result.has_quota:
        raise HTTPException(status_code=402, detail=quota_result.error_message)

    # Start the campaign using the runner service
    try:
        await campaign_runner_service.start_campaign(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Get updated campaign
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    workflow_name = await db_client.get_workflow_name(campaign.workflow_id, user.id)

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
    workflow_name = await db_client.get_workflow_name(campaign.workflow_id, user.id)

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

    if request.max_concurrency is not None:
        await _validate_max_concurrency(
            request.max_concurrency, user.selected_organization_id
        )

    # Build update kwargs
    update_kwargs = {}

    if request.name is not None:
        update_kwargs["name"] = request.name

    if request.retry_config is not None:
        update_kwargs["retry_config"] = request.retry_config.model_dump()

    # Merge max_concurrency and schedule_config into orchestrator_metadata
    metadata = campaign.orchestrator_metadata or {}
    metadata_changed = False

    if request.max_concurrency is not None:
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
    workflow_name = await db_client.get_workflow_name(campaign.workflow_id, user.id)

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


@router.get("/{campaign_id}/runs")
async def get_campaign_runs(
    campaign_id: int,
    page: int = 1,
    limit: int = 50,
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

    workflow_name = await db_client.get_workflow_name(child.workflow_id, user.id)
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
    quota_result = await check_dograh_quota(user, workflow_id=campaign.workflow_id)
    if not quota_result.has_quota:
        raise HTTPException(status_code=402, detail=quota_result.error_message)

    # Resume the campaign using the runner service
    try:
        await campaign_runner_service.resume_campaign(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Get updated campaign
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    workflow_name = await db_client.get_workflow_name(campaign.workflow_id, user.id)

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

    Only works for CSV source type. For Google Sheets, use the source_id directly.
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
