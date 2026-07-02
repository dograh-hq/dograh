import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from api.constants import AUTH_PROVIDER
from api.db import db_client
from api.db.models import UserModel
from api.schemas.auth import UserResponse
from api.services.auth.depends import get_superuser
from api.services.auth.stack_auth import stackauth
from api.utils.auth import create_jwt_token

router = APIRouter(prefix="/superuser", tags=["superuser"])


class ImpersonateRequest(BaseModel):
    """Request payload for superadmin impersonation.

    Either ``provider_user_id`` **or** ``user_id`` must be supplied. If both are
    provided, ``provider_user_id`` takes precedence.
    """

    provider_user_id: str | None = None
    user_id: int | None = None


class ImpersonateResponse(BaseModel):
    """Impersonation session tokens.

    ``provider`` tells the UI which cookie flow to run: Stack returns both
    tokens (refresh cookie flow); local mode returns an OSS JWT in
    ``access_token`` plus the target ``user`` for the session cookie, and no
    refresh token.
    """

    provider: str = "stack"  # "stack" | "local"
    refresh_token: str | None = None
    access_token: str
    user: UserResponse | None = None  # local mode only


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


async def _impersonate_local(
    request: ImpersonateRequest, superuser: UserModel
) -> ImpersonateResponse:
    """Local (OSS email/password) impersonation: mint an OSS JWT for the target.

    Accepts the same identifiers as the Stack path (``provider_user_id`` takes
    precedence over ``user_id``); an email pasted into the provider-id field is
    resolved too. Refuses to impersonate another superuser.
    """
    target: UserModel | None = None
    if request.provider_user_id:
        target = await db_client.get_user_by_provider_id(request.provider_user_id)
        if target is None and "@" in request.provider_user_id:
            target = await db_client.get_user_by_email(request.provider_user_id)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User '{request.provider_user_id}' not found.",
            )
    elif request.user_id is not None:
        target = await db_client.get_user_by_id(request.user_id)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with ID {request.user_id} not found.",
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'provider_user_id' or 'user_id' must be provided.",
        )

    if target.is_superuser and target.id != superuser.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Refusing to impersonate another superuser.",
        )

    access_token = create_jwt_token(target.id, target.email or "")
    return ImpersonateResponse(
        provider="local",
        access_token=access_token,
        user=UserResponse(
            id=target.id,
            email=target.email,
            organization_id=target.selected_organization_id,
            provider_id=target.provider_id,
            is_superuser=bool(target.is_superuser),
        ),
    )


@router.post("/impersonate")
async def impersonate(
    request: ImpersonateRequest, user: UserModel = Depends(get_superuser)
) -> ImpersonateResponse:
    """Impersonate a user as a super-admin.
    Internally, Stack Auth requires the **provider user ID** (a UUID-ish string)
    to create an impersonation session. In local auth mode we instead mint an
    OSS JWT for the target user (``provider: "local"`` in the response).
    """

    if AUTH_PROVIDER == "local":
        return await _impersonate_local(request, user)

    provider_user_id: str | None = request.provider_user_id

    # ------------------------------------------------------------------
    # Fallback: resolve provider_user_id from internal ``user_id``
    # ------------------------------------------------------------------
    if provider_user_id is None:
        if request.user_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either 'provider_user_id' or 'user_id' must be provided.",
            )

        db_user = await db_client.get_user_by_id(request.user_id)

        if db_user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with ID {request.user_id} not found.",
            )

        provider_user_id = db_user.provider_id

    # ------------------------------------------------------------------
    # Call Stack Auth to create the impersonation session
    # ------------------------------------------------------------------
    session = await stackauth.impersonate(provider_user_id)

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
