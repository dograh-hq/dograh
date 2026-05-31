import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_superuser

router = APIRouter(prefix="/superuser", tags=["superuser"])


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
