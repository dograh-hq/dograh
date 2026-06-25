from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.reports import DailyReportService

router = APIRouter(prefix="/organizations/reports")


class DailyReportResponse(BaseModel):
    date: str
    timezone: str
    workflow_id: Optional[int]
    metrics: Dict[str, int]
    disposition_distribution: List[Dict[str, Any]]
    call_duration_distribution: List[Dict[str, Any]]


class WorkflowOption(BaseModel):
    id: int
    name: str


class WorkflowRunDetail(BaseModel):
    phone_number: str
    disposition: str
    duration_seconds: float
    workflow_id: int
    run_id: int
    workflow_name: str
    created_at: str


@router.get("/daily", response_model=DailyReportResponse)
async def get_daily_report(
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    timezone: str = Query(..., description="IANA timezone (e.g., 'America/New_York')"),
    workflow_id: Optional[int] = Query(
        None, description="Optional workflow ID to filter by"
    ),
    user: UserModel = Depends(get_user),
) -> DailyReportResponse:
    """
    Get daily report for the specified date and timezone.
    If workflow_id is provided, filters results to that specific workflow.
    If workflow_id is None, includes all workflows for the organization.
    """
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    # Validate date format
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid date format. Use YYYY-MM-DD"
        )

    report_service = DailyReportService()

    try:
        report = await report_service.get_daily_report(
            organization_id=user.selected_organization_id,
            date=date,
            timezone=timezone,
            workflow_id=workflow_id,
        )
        return DailyReportResponse(**report)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class AgentLeaderboardRow(BaseModel):
    workflow_id: int
    workflow_name: str
    total_runs: int
    total_minutes: float
    avg_duration_seconds: float
    transfer_rate_percent: float
    last_run_at: Optional[str]


class AgentLeaderboardResponse(BaseModel):
    days: int
    agents: List[AgentLeaderboardRow]


@router.get("/agent-leaderboard", response_model=AgentLeaderboardResponse)
async def get_agent_leaderboard(
    days: int = Query(7, ge=1, le=90, description="Look-back window in days"),
    user: UserModel = Depends(get_user),
) -> AgentLeaderboardResponse:
    """Per-agent performance over the last N days (call count, avg duration,
    total minutes, transfer rate, last run) — for the Reports analytics view."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    report_service = DailyReportService()
    try:
        result = await report_service.get_agent_leaderboard(
            organization_id=user.selected_organization_id, days=days
        )
        return AgentLeaderboardResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workflows", response_model=List[WorkflowOption])
async def get_workflow_options(
    user: UserModel = Depends(get_user),
) -> List[WorkflowOption]:
    """
    Get all workflows for the user's organization.
    Used to populate the workflow selector dropdown in the reports page.
    """
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    report_service = DailyReportService()

    workflows = await report_service.get_workflows_for_organization(
        organization_id=user.selected_organization_id
    )

    return [WorkflowOption(**w) for w in workflows]


@router.get("/daily/runs", response_model=List[WorkflowRunDetail])
async def get_daily_runs_detail(
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    timezone: str = Query(..., description="IANA timezone (e.g., 'America/New_York')"),
    workflow_id: Optional[int] = Query(
        None, description="Optional workflow ID to filter by"
    ),
    user: UserModel = Depends(get_user),
) -> List[WorkflowRunDetail]:
    """
    Get detailed workflow runs for the specified date.
    Used for CSV export functionality.
    """
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    # Validate date format
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid date format. Use YYYY-MM-DD"
        )

    report_service = DailyReportService()

    try:
        runs = await report_service.get_daily_runs_detail(
            organization_id=user.selected_organization_id,
            date=date,
            timezone=timezone,
            workflow_id=workflow_id,
        )
        return [WorkflowRunDetail(**run) for run in runs]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
