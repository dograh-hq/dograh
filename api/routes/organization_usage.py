import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

from api.constants import DEPLOYMENT_MODE
from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.mps_service_key_client import mps_service_key_client

router = APIRouter(prefix="/organizations")


class CurrentUsageResponse(BaseModel):
    period_start: str
    period_end: str
    used_dograh_tokens: float
    quota_dograh_tokens: int
    percentage_used: float
    next_refresh_date: str
    quota_enabled: bool
    total_duration_seconds: int
    # New USD fields
    used_amount_usd: Optional[float] = None
    quota_amount_usd: Optional[float] = None
    currency: Optional[str] = None
    price_per_second_usd: Optional[float] = None


class MPSCreditsResponse(BaseModel):
    total_credits_used: float
    remaining_credits: float
    total_quota: float


class WorkflowRunUsageResponse(BaseModel):
    id: int
    workflow_id: int
    workflow_name: Optional[str]
    name: str
    created_at: str
    dograh_token_usage: float
    call_duration_seconds: int
    recording_url: Optional[str] = None
    transcript_url: Optional[str] = None
    phone_number: Optional[str] = None
    call_type: Optional[str] = None
    disposition: Optional[str] = None
    initial_context: Optional[Dict[str, Any]] = None
    gathered_context: Optional[Dict[str, Any]] = None
    # New USD field
    charge_usd: Optional[float] = None


class UsageHistoryResponse(BaseModel):
    runs: List[WorkflowRunUsageResponse]
    total_dograh_tokens: float
    total_duration_seconds: int
    total_count: int
    page: int
    limit: int
    total_pages: int


class DailyUsageItem(BaseModel):
    date: str
    minutes: float
    cost_usd: Optional[float] = None
    dograh_tokens: float
    call_count: int


class DailyUsageBreakdownResponse(BaseModel):
    breakdown: List[DailyUsageItem]
    total_minutes: float
    total_cost_usd: Optional[float] = None
    total_dograh_tokens: float
    currency: Optional[str] = None


@router.get("/usage/current-period", response_model=CurrentUsageResponse)
async def get_current_period_usage(user: UserModel = Depends(get_user)):
    """Get current billing period usage for the user's organization."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    try:
        usage = await db_client.get_current_usage(user.selected_organization_id)
        return usage
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/usage/mps-credits", response_model=MPSCreditsResponse)
async def get_mps_credits(user: UserModel = Depends(get_user)):
    """Get aggregated usage and quota from MPS.

    OSS users: queries by provider_id (created_by).
    Hosted users: queries by organization_id.
    """
    try:
        if DEPLOYMENT_MODE == "oss":
            usage = await mps_service_key_client.get_usage_by_created_by(
                str(user.provider_id)
            )
        else:
            if not user.selected_organization_id:
                raise HTTPException(status_code=400, detail="No organization selected")
            usage = await mps_service_key_client.get_usage_by_organization(
                user.selected_organization_id
            )

        total_used = usage.get("total_credits_used", 0.0)
        total_remaining = usage.get("remaining_credits", 0.0)

        return MPSCreditsResponse(
            total_credits_used=total_used,
            remaining_credits=total_remaining,
            total_quota=total_used + total_remaining,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch MPS credits: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/usage/runs", response_model=UsageHistoryResponse)
async def get_usage_history(
    start_date: Optional[str] = Query(None, description="ISO format date string"),
    end_date: Optional[str] = Query(None, description="ISO format date string"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    filters: Optional[str] = Query(None, description="JSON string of filters"),
    user: UserModel = Depends(get_user),
):
    """Get paginated workflow runs with usage for the organization."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    # Parse dates if provided
    start_dt = datetime.fromisoformat(start_date) if start_date else None
    end_dt = datetime.fromisoformat(end_date) if end_date else None

    # Parse filters if provided
    parsed_filters = None
    if filters:
        try:
            parsed_filters = json.loads(filters)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid filters format")

    try:
        offset = (page - 1) * limit
        (
            runs,
            total_count,
            total_tokens,
            total_duration,
        ) = await db_client.get_usage_history(
            user.selected_organization_id,
            start_date=start_dt,
            end_date=end_dt,
            limit=limit,
            offset=offset,
            filters=parsed_filters,
        )

        total_pages = (total_count + limit - 1) // limit

        return {
            "runs": runs,
            "total_dograh_tokens": total_tokens,
            "total_duration_seconds": total_duration,
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/usage/daily-breakdown", response_model=DailyUsageBreakdownResponse)
async def get_daily_usage_breakdown(
    days: int = Query(7, ge=1, le=30, description="Number of days to include"),
    user: UserModel = Depends(get_user),
):
    """Get daily usage breakdown for the last N days. Only available for organizations with pricing."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    try:
        # Get organization to check if it has pricing
        org = await db_client.get_organization_by_id(user.selected_organization_id)
        if not org or org.price_per_second_usd is None:
            raise HTTPException(
                status_code=400,
                detail="Daily breakdown is only available for organizations with pricing configured",
            )

        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days - 1)
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)

        # Get daily breakdown
        breakdown = await db_client.get_daily_usage_breakdown(
            user.selected_organization_id,
            start_date,
            end_date,
            org.price_per_second_usd,
            user_id=user.id,
        )

        return breakdown
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
