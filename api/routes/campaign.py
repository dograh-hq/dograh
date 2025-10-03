from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationConfigurationKey
from api.services.auth.depends import get_user
from api.services.campaign.runner import campaign_runner_service

router = APIRouter(prefix="/campaign")


class CreateCampaignRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    workflow_id: int
    source_id: str  # Sheet URL


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


class CampaignsResponse(BaseModel):
    campaigns: List[CampaignResponse]


class WorkflowRunResponse(BaseModel):
    id: int
    workflow_id: int
    state: str
    created_at: datetime
    completed_at: Optional[datetime]


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

    campaign = await db_client.create_campaign(
        name=request.name,
        workflow_id=request.workflow_id,
        source_type="google-sheet",
        source_id=request.source_id,
        user_id=user.id,
        organization_id=user.selected_organization_id,
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

    campaign_responses = [
        CampaignResponse(
            id=c.id,
            name=c.name,
            workflow_id=c.workflow_id,
            workflow_name=workflow_map.get(c.workflow_id, "Unknown"),
            state=c.state,
            source_type=c.source_type,
            source_id=c.source_id,
            total_rows=c.total_rows,
            processed_rows=c.processed_rows,
            failed_rows=c.failed_rows,
            created_at=c.created_at,
            started_at=c.started_at,
            completed_at=c.completed_at,
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

    return CampaignResponse(
        id=campaign.id,
        name=campaign.name,
        workflow_id=campaign.workflow_id,
        workflow_name=workflow_name or "Unknown",
        state=campaign.state,
        source_type=campaign.source_type,
        source_id=campaign.source_id,
        total_rows=campaign.total_rows,
        processed_rows=campaign.processed_rows,
        failed_rows=campaign.failed_rows,
        created_at=campaign.created_at,
        started_at=campaign.started_at,
        completed_at=campaign.completed_at,
    )


@router.post("/{campaign_id}/start")
async def start_campaign(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Start campaign execution"""
    # Check if organization has TWILIO_CONFIGURATION configured
    twilio_config = await db_client.get_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.TWILIO_CONFIGURATION.value,
    )

    if (
        not twilio_config
        or not twilio_config.value
        or not twilio_config.value.get("value")
    ):
        raise HTTPException(
            status_code=401,
            detail="Your organisation is not allowed to make phone call. Contact founders@dograh.com for further support.",
        )

    # Verify campaign exists and belongs to organization
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Start the campaign using the runner service
    try:
        await campaign_runner_service.start_campaign(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Get updated campaign
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    workflow_name = await db_client.get_workflow_name(campaign.workflow_id, user.id)

    return CampaignResponse(
        id=campaign.id,
        name=campaign.name,
        workflow_id=campaign.workflow_id,
        workflow_name=workflow_name or "Unknown",
        state=campaign.state,
        source_type=campaign.source_type,
        source_id=campaign.source_id,
        total_rows=campaign.total_rows,
        processed_rows=campaign.processed_rows,
        failed_rows=campaign.failed_rows,
        created_at=campaign.created_at,
        started_at=campaign.started_at,
        completed_at=campaign.completed_at,
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

    return CampaignResponse(
        id=campaign.id,
        name=campaign.name,
        workflow_id=campaign.workflow_id,
        workflow_name=workflow_name or "Unknown",
        state=campaign.state,
        source_type=campaign.source_type,
        source_id=campaign.source_id,
        total_rows=campaign.total_rows,
        processed_rows=campaign.processed_rows,
        failed_rows=campaign.failed_rows,
        created_at=campaign.created_at,
        started_at=campaign.started_at,
        completed_at=campaign.completed_at,
    )


@router.get("/{campaign_id}/runs")
async def get_campaign_runs(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> List[WorkflowRunResponse]:
    """Get campaign workflow runs"""
    runs = await db_client.get_campaign_runs(campaign_id, user.selected_organization_id)

    return [
        WorkflowRunResponse(
            id=run.id,
            workflow_id=run.workflow_id,
            state="completed" if run.is_completed else "running",
            created_at=run.created_at,
            completed_at=run.created_at if run.is_completed else None,
        )
        for run in runs
    ]


@router.post("/{campaign_id}/resume")
async def resume_campaign(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Resume a paused campaign"""
    # Check if organization has TWILIO_CONFIGURATION configured
    twilio_config = await db_client.get_configuration(
        user.selected_organization_id,
        OrganizationConfigurationKey.TWILIO_CONFIGURATION.value,
    )

    if (
        not twilio_config
        or not twilio_config.value
        or not twilio_config.value.get("value")
    ):
        raise HTTPException(
            status_code=401,
            detail="Your organisation is not allowed to make phone call. Contact founders@dograh.com for further support.",
        )

    # Verify campaign exists and belongs to organization
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Resume the campaign using the runner service
    try:
        await campaign_runner_service.resume_campaign(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Get updated campaign
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    workflow_name = await db_client.get_workflow_name(campaign.workflow_id, user.id)

    return CampaignResponse(
        id=campaign.id,
        name=campaign.name,
        workflow_id=campaign.workflow_id,
        workflow_name=workflow_name or "Unknown",
        state=campaign.state,
        source_type=campaign.source_type,
        source_id=campaign.source_id,
        total_rows=campaign.total_rows,
        processed_rows=campaign.processed_rows,
        failed_rows=campaign.failed_rows,
        created_at=campaign.created_at,
        started_at=campaign.started_at,
        completed_at=campaign.completed_at,
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
