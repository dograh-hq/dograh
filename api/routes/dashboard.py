from datetime import UTC, datetime, time, timedelta
from typing import Any, Dict, List, Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import Float, Integer, String, and_, case, desc, func, or_, select

from api.db import db_client
from api.db.models import (
    CampaignModel,
    OrganizationModel,
    TelephonyConfigurationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import WorkflowRunState, WorkflowStatus
from api.services.auth.depends import get_user_with_selected_organization
from api.services.call_concurrency import call_concurrency

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# Response Schemas
# ---------------------------------------------------------------------------


class KpiComparison(BaseModel):
    total_calls_change_pct: Optional[float] = None
    connected_calls_change_pct: Optional[float] = None
    talk_time_change_pct: Optional[float] = None
    success_rate_change_pct: Optional[float] = None


class DashboardOverviewKpi(BaseModel):
    total_calls: int = 0
    connected_calls: int = 0
    successful_calls: int = 0
    talk_time_seconds: float = 0.0
    success_rate_pct: float = 0.0
    connected_rate_pct: float = 0.0
    total_spend_usd: float = 0.0
    wallet_balance_usd: float = 0.0
    active_calls: int = 0
    subscription_tier: str = "pay_as_you_go"
    subscription_status: str = "active"
    comparison: KpiComparison = Field(default_factory=KpiComparison)


class CallActivityPoint(BaseModel):
    timestamp: str
    label: str
    total_calls: int = 0
    connected_calls: int = 0
    successful_calls: int = 0


class AgentPerformanceItem(BaseModel):
    id: int
    name: str
    status: str
    total_calls: int = 0
    successful_calls: int = 0
    success_rate_pct: float = 0.0
    talk_time_seconds: float = 0.0


class CampaignSummaryItem(BaseModel):
    id: int
    name: str
    workflow_id: int
    workflow_name: Optional[str] = None
    state: str
    total_rows: int = 0
    processed_rows: int = 0
    failed_rows: int = 0
    progress_pct: float = 0.0
    updated_at: Optional[str] = None


class RecentCallItem(BaseModel):
    id: int
    contact: str
    agent_id: int
    agent_name: str
    duration_seconds: float = 0.0
    outcome: str = "COMPLETED"
    call_type: str = "outbound"
    state: str = "completed"
    created_at: str


class UsageSummary(BaseModel):
    total_duration_minutes: float = 0.0
    estimated_spend_usd: float = 0.0
    monthly_minutes_used: float = 0.0
    monthly_minutes_limit: Optional[float] = None
    wallet_balance_usd: float = 0.0
    subscription_tier: str = "pay_as_you_go"
    active_concurrent_calls: int = 0


class AttentionItem(BaseModel):
    id: str
    severity: Literal["error", "warning", "info"]
    title: str
    description: str
    cta_text: str
    cta_href: str


class DashboardOverviewResponse(BaseModel):
    range_preset: str
    timezone: str
    period_start: str
    period_end: str
    overview: DashboardOverviewKpi
    call_activity: List[CallActivityPoint]
    agent_performance: List[AgentPerformanceItem]
    campaigns: List[CampaignSummaryItem]
    recent_calls: List[RecentCallItem]
    usage: UsageSummary
    attention_items: List[AttentionItem]
    last_updated: str


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _calc_pct_change(current: float, previous: float) -> Optional[float]:
    if previous <= 0:
        return None if current == 0 else 100.0
    return round(((current - previous) / previous) * 100.0, 1)


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None:
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Dashboard Overview Route
# ---------------------------------------------------------------------------


@router.get("/overview", response_model=DashboardOverviewResponse)
async def get_dashboard_overview(
    range_preset: str = Query(
        "7d",
        description="Date range preset: 'today', '7d', '30d', '90d', or 'custom'",
    ),
    timezone: str = Query(
        "UTC",
        description="IANA timezone name, e.g. 'America/New_York' or 'Asia/Kolkata'",
    ),
    start_date: Optional[str] = Query(None, description="ISO datetime for custom start"),
    end_date: Optional[str] = Query(None, description="ISO datetime for custom end"),
    user: UserModel = Depends(get_user_with_selected_organization),
) -> DashboardOverviewResponse:
    organization_id = user.selected_organization_id
    if not organization_id:
        raise HTTPException(status_code=400, detail="No active organization selected")

    # 1. Resolve Timezone
    try:
        user_tz = ZoneInfo(timezone)
    except Exception:
        try:
            user_tz = ZoneInfo("UTC")
            timezone = "UTC"
        except Exception:
            user_tz = UTC
            timezone = "UTC"

    now_tz = datetime.now(user_tz)

    # 2. Determine Date Range
    if range_preset == "today":
        start_tz = datetime.combine(now_tz.date(), time.min, tzinfo=user_tz)
        end_tz = now_tz
    elif range_preset == "30d":
        start_tz = now_tz - timedelta(days=30)
        end_tz = now_tz
    elif range_preset == "90d":
        start_tz = now_tz - timedelta(days=90)
        end_tz = now_tz
    elif range_preset == "custom" and start_date and end_date:
        try:
            start_tz = datetime.fromisoformat(start_date.replace("Z", "+00:00")).astimezone(user_tz)
            end_tz = datetime.fromisoformat(end_date.replace("Z", "+00:00")).astimezone(user_tz)
        except Exception:
            start_tz = now_tz - timedelta(days=7)
            end_tz = now_tz
            range_preset = "7d"
    else:  # default '7d'
        start_tz = now_tz - timedelta(days=7)
        end_tz = now_tz
        range_preset = "7d"

    start_utc = start_tz.astimezone(UTC)
    end_utc = end_tz.astimezone(UTC)

    # Calculate previous equivalent period for comparison
    period_duration = end_utc - start_utc
    prev_end_utc = start_utc
    prev_start_utc = start_utc - period_duration

    async with db_client.async_session() as session:
        # -------------------------------------------------------------------
        # Organization Info & Wallet Balance
        # -------------------------------------------------------------------
        org_result = await session.execute(
            select(OrganizationModel).where(OrganizationModel.id == organization_id)
        )
        organization = org_result.scalars().first()
        wallet_balance = _safe_float(organization.wallet_balance_usd if organization else 0.0)
        subscription_tier = organization.subscription_tier if organization else "pay_as_you_go"
        subscription_status = organization.subscription_status if organization else "active"
        monthly_minutes_used = _safe_float(organization.monthly_minutes_used if organization else 0.0)
        custom_monthly_minutes = organization.custom_monthly_minutes if organization else None

        # -------------------------------------------------------------------
        # Active Calls via Redis Call Concurrency
        # -------------------------------------------------------------------
        try:
            active_calls = await call_concurrency.get_org_active_calls(organization_id)
        except Exception:
            active_calls = 0

        # -------------------------------------------------------------------
        # Current Period Runs Query (Workspace-scoped)
        # -------------------------------------------------------------------
        current_runs_query = (
            select(
                WorkflowRunModel.id,
                WorkflowRunModel.workflow_id,
                WorkflowRunModel.state,
                WorkflowRunModel.is_completed,
                WorkflowRunModel.created_at,
                func.coalesce(
                    func.replace(
                        func.replace(
                            func.cast(WorkflowRunModel.usage_info["call_duration_seconds"], String),
                            '"',
                            "",
                        ),
                        "'",
                        "",
                    ),
                    "0",
                ).label("duration_str"),
                func.coalesce(
                    func.replace(
                        func.replace(
                            func.cast(WorkflowRunModel.cost_info["total_cost_usd"], String),
                            '"',
                            "",
                        ),
                        "'",
                        "",
                    ),
                    "0",
                ).label("cost_str"),
                func.coalesce(
                    func.replace(
                        func.replace(
                            func.cast(WorkflowRunModel.gathered_context["mapped_call_disposition"], String),
                            '"',
                            "",
                        ),
                        "'",
                        "",
                    ),
                    "UNKNOWN",
                ).label("disposition"),
            )
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(
                and_(
                    WorkflowModel.organization_id == organization_id,
                    WorkflowRunModel.created_at >= start_utc,
                    WorkflowRunModel.created_at <= end_utc,
                )
            )
        )
        current_runs_result = await session.execute(current_runs_query)
        current_runs = current_runs_result.all()

        # Compute current period KPIs
        curr_total_calls = len(current_runs)
        curr_connected_calls = 0
        curr_successful_calls = 0
        curr_talk_time_seconds = 0.0
        curr_total_spend = 0.0

        FAILED_DISPOSITIONS = {"FAILED", "ERROR", "NO_ANSWER", "BUSY", "CANCELED"}
        SUCCESS_DISPOSITIONS = {"COMPLETED", "XFER", "INTERESTED", "CONVERTED", "QUALIFIED"}

        for run in current_runs:
            dur = _safe_float(run.duration_str)
            cost = _safe_float(run.cost_str)
            disp = (run.disposition or "").upper()
            curr_talk_time_seconds += dur
            curr_total_spend += cost

            if dur > 0 or disp not in FAILED_DISPOSITIONS:
                curr_connected_calls += 1

            if disp in SUCCESS_DISPOSITIONS or (run.is_completed and dur > 10 and disp not in FAILED_DISPOSITIONS):
                curr_successful_calls += 1

        curr_success_rate = (
            round((curr_successful_calls / curr_total_calls) * 100.0, 1)
            if curr_total_calls > 0
            else 0.0
        )
        curr_connected_rate = (
            round((curr_connected_calls / curr_total_calls) * 100.0, 1)
            if curr_total_calls > 0
            else 0.0
        )

        # -------------------------------------------------------------------
        # Previous Period Runs (for percentage comparisons)
        # -------------------------------------------------------------------
        prev_runs_query = (
            select(
                WorkflowRunModel.id,
                WorkflowRunModel.is_completed,
                func.coalesce(
                    func.replace(
                        func.replace(
                            func.cast(WorkflowRunModel.usage_info["call_duration_seconds"], String),
                            '"',
                            "",
                        ),
                        "'",
                        "",
                    ),
                    "0",
                ).label("duration_str"),
            )
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(
                and_(
                    WorkflowModel.organization_id == organization_id,
                    WorkflowRunModel.created_at >= prev_start_utc,
                    WorkflowRunModel.created_at < prev_end_utc,
                )
            )
        )
        prev_runs_result = await session.execute(prev_runs_query)
        prev_runs = prev_runs_result.all()

        prev_total = float(len(prev_runs))
        prev_connected = 0.0
        prev_completed = 0.0
        prev_talk = 0.0

        for pr in prev_runs:
            dur = _safe_float(pr.duration_str)
            prev_talk += dur
            if dur > 0:
                prev_connected += 1.0
            if pr.is_completed:
                prev_completed += 1.0

        prev_success_rate = (prev_completed / prev_total * 100.0) if prev_total > 0 else 0.0

        comparison = KpiComparison(
            total_calls_change_pct=_calc_pct_change(curr_total_calls, prev_total),
            connected_calls_change_pct=_calc_pct_change(curr_connected_calls, prev_connected),
            talk_time_change_pct=_calc_pct_change(curr_talk_time_seconds, prev_talk),
            success_rate_change_pct=_calc_pct_change(curr_success_rate, prev_success_rate),
        )

        # -------------------------------------------------------------------
        # Call Activity Over Time
        # -------------------------------------------------------------------
        activity_points: List[CallActivityPoint] = []
        is_hourly = range_preset == "today" or (end_utc - start_utc).total_seconds() <= 86400

        buckets: Dict[str, Dict[str, Any]] = {}
        if is_hourly:
            # Generate hourly slots across the range
            cursor = start_tz.replace(minute=0, second=0, microsecond=0)
            while cursor <= end_tz:
                key = cursor.strftime("%Y-%m-%d %H:00")
                label = cursor.strftime("%I %p")
                buckets[key] = {"label": label, "timestamp": cursor.isoformat(), "total": 0, "connected": 0, "successful": 0}
                cursor += timedelta(hours=1)
        else:
            # Generate daily slots across the range
            cursor = start_tz.date()
            end_date_limit = end_tz.date()
            while cursor <= end_date_limit:
                key = cursor.strftime("%Y-%m-%d")
                label = cursor.strftime("%b %d")
                buckets[key] = {"label": label, "timestamp": cursor.isoformat(), "total": 0, "connected": 0, "successful": 0}
                cursor += timedelta(days=1)

        # Populate buckets from current_runs
        for run in current_runs:
            run_dt_local = run.created_at.astimezone(user_tz)
            if is_hourly:
                bucket_key = run_dt_local.strftime("%Y-%m-%d %H:00")
            else:
                bucket_key = run_dt_local.strftime("%Y-%m-%d")

            if bucket_key in buckets:
                dur = _safe_float(run.duration_str)
                disp = (run.disposition or "").upper()
                buckets[bucket_key]["total"] += 1
                if dur > 0 or disp not in FAILED_DISPOSITIONS:
                    buckets[bucket_key]["connected"] += 1
                if disp in SUCCESS_DISPOSITIONS or (run.is_completed and dur > 10 and disp not in FAILED_DISPOSITIONS):
                    buckets[bucket_key]["successful"] += 1

        for b in buckets.values():
            activity_points.append(
                CallActivityPoint(
                    timestamp=b["timestamp"],
                    label=b["label"],
                    total_calls=b["total"],
                    connected_calls=b["connected"],
                    successful_calls=b["successful"],
                )
            )

        # -------------------------------------------------------------------
        # Agent Performance
        # -------------------------------------------------------------------
        agents_query = (
            select(WorkflowModel)
            .where(WorkflowModel.organization_id == organization_id)
            .order_by(WorkflowModel.created_at.desc())
        )
        agents_result = (await session.execute(agents_query)).scalars().all()

        agent_runs_map: Dict[int, Dict[str, Any]] = {}
        for agent in agents_result:
            agent_runs_map[agent.id] = {
                "name": agent.name,
                "status": agent.status or "active",
                "total_calls": 0,
                "successful_calls": 0,
                "talk_time_seconds": 0.0,
            }

        for run in current_runs:
            if run.workflow_id in agent_runs_map:
                dur = _safe_float(run.duration_str)
                disp = (run.disposition or "").upper()
                agent_runs_map[run.workflow_id]["total_calls"] += 1
                agent_runs_map[run.workflow_id]["talk_time_seconds"] += dur
                if disp in SUCCESS_DISPOSITIONS or (run.is_completed and dur > 10 and disp not in FAILED_DISPOSITIONS):
                    agent_runs_map[run.workflow_id]["successful_calls"] += 1

        agent_performance: List[AgentPerformanceItem] = []
        for agent_id, data in sorted(agent_runs_map.items(), key=lambda x: x[1]["total_calls"], reverse=True):
            tot = data["total_calls"]
            suc = data["successful_calls"]
            rate = round((suc / tot) * 100.0, 1) if tot > 0 else 0.0
            agent_performance.append(
                AgentPerformanceItem(
                    id=agent_id,
                    name=data["name"],
                    status=data["status"],
                    total_calls=tot,
                    successful_calls=suc,
                    success_rate_pct=rate,
                    talk_time_seconds=round(data["talk_time_seconds"], 1),
                )
            )

        # -------------------------------------------------------------------
        # Campaigns Overview
        # -------------------------------------------------------------------
        campaigns_query = (
            select(
                CampaignModel.id,
                CampaignModel.name,
                CampaignModel.workflow_id,
                CampaignModel.state,
                CampaignModel.total_rows,
                CampaignModel.processed_rows,
                CampaignModel.failed_rows,
                CampaignModel.updated_at,
                WorkflowModel.name.label("workflow_name"),
            )
            .outerjoin(WorkflowModel, CampaignModel.workflow_id == WorkflowModel.id)
            .where(CampaignModel.organization_id == organization_id)
            .order_by(CampaignModel.updated_at.desc())
            .limit(6)
        )
        campaigns_res = (await session.execute(campaigns_query)).all()
        campaign_items: List[CampaignSummaryItem] = []
        for c in campaigns_res:
            tot = c.total_rows or 0
            proc = c.processed_rows or 0
            prog = round((proc / tot * 100.0), 1) if tot > 0 else 0.0
            campaign_items.append(
                CampaignSummaryItem(
                    id=c.id,
                    name=c.name,
                    workflow_id=c.workflow_id,
                    workflow_name=c.workflow_name,
                    state=str(c.state),
                    total_rows=tot,
                    processed_rows=proc,
                    failed_rows=c.failed_rows or 0,
                    progress_pct=prog,
                    updated_at=c.updated_at.isoformat() if c.updated_at else None,
                )
            )

        # -------------------------------------------------------------------
        # Recent Calls (Last 10)
        # -------------------------------------------------------------------
        recent_calls_query = (
            select(
                WorkflowRunModel.id,
                WorkflowRunModel.workflow_id,
                WorkflowRunModel.call_type,
                WorkflowRunModel.state,
                WorkflowRunModel.created_at,
                WorkflowModel.name.label("agent_name"),
                func.coalesce(
                    func.replace(
                        func.replace(
                            func.cast(WorkflowRunModel.usage_info["call_duration_seconds"], String),
                            '"',
                            "",
                        ),
                        "'",
                        "",
                    ),
                    "0",
                ).label("duration_str"),
                func.coalesce(
                    func.replace(
                        func.replace(
                            func.cast(WorkflowRunModel.gathered_context["mapped_call_disposition"], String),
                            '"',
                            "",
                        ),
                        "'",
                        "",
                    ),
                    "COMPLETED",
                ).label("disposition"),
                func.coalesce(
                    func.replace(
                        func.replace(
                            func.cast(WorkflowRunModel.gathered_context["customer_phone_number"], String),
                            '"',
                            "",
                        ),
                        "'",
                        "",
                    ),
                    func.replace(
                        func.replace(
                            func.cast(WorkflowRunModel.initial_context["phone_number"], String),
                            '"',
                            "",
                        ),
                        "'",
                        "",
                    ),
                    "Direct Call",
                ).label("contact"),
            )
            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)
            .where(WorkflowModel.organization_id == organization_id)
            .order_by(WorkflowRunModel.created_at.desc())
            .limit(10)
        )
        recent_runs_res = (await session.execute(recent_calls_query)).all()
        recent_calls: List[RecentCallItem] = []
        for r in recent_runs_res:
            dur = _safe_float(r.duration_str)
            recent_calls.append(
                RecentCallItem(
                    id=r.id,
                    contact=r.contact if r.contact and r.contact != '""' else "Direct Call",
                    agent_id=r.workflow_id,
                    agent_name=r.agent_name or "Voice Agent",
                    duration_seconds=dur,
                    outcome=r.disposition if r.disposition and r.disposition != '""' else "COMPLETED",
                    call_type=str(r.call_type),
                    state=str(r.state),
                    created_at=r.created_at.isoformat(),
                )
            )

        # -------------------------------------------------------------------
        # Needs Attention Items (Real System Conditions)
        # -------------------------------------------------------------------
        attention_items: List[AttentionItem] = []

        # Condition 1: Low wallet balance
        if wallet_balance < 5.0:
            attention_items.append(
                AttentionItem(
                    id="low-balance",
                    severity="error" if wallet_balance <= 0.5 else "warning",
                    title="Low Wallet Balance",
                    description=f"Your balance is ${wallet_balance:.2f}. Calls will pause when credits are exhausted.",
                    cta_text="Add Credits",
                    cta_href="/billing",
                )
            )

        # Condition 2: Check for failed campaigns
        failed_campaigns = [c for c in campaign_items if c.state == "failed"]
        if failed_campaigns:
            attention_items.append(
                AttentionItem(
                    id=f"failed-campaign-{failed_campaigns[0].id}",
                    severity="error",
                    title=f"Campaign '{failed_campaigns[0].name}' Failed",
                    description="Outbound dialing halted due to dispatch errors or invalid lead numbers.",
                    cta_text="Inspect Campaign",
                    cta_href=f"/campaigns/{failed_campaigns[0].id}",
                )
            )

        # Condition 3: Check telephony configuration count
        telephony_cfg_count = (
            await session.execute(
                select(func.count(TelephonyConfigurationModel.id)).where(
                    TelephonyConfigurationModel.organization_id == organization_id
                )
            )
        ).scalar() or 0

        if telephony_cfg_count == 0:
            attention_items.append(
                AttentionItem(
                    id="missing-telephony",
                    severity="warning",
                    title="No Telephony Provider Configured",
                    description="Connect Twilio, Telnyx, Plivo, or a SIP trunk to dial numbers and receive calls.",
                    cta_text="Configure Telephony",
                    cta_href="/telephony-configurations",
                )
            )

        # Condition 4: High failure rate agent
        for ag in agent_performance:
            if ag.total_calls >= 5 and ag.success_rate_pct < 40.0:
                attention_items.append(
                    AttentionItem(
                        id=f"agent-high-fail-{ag.id}",
                        severity="warning",
                        title=f"High Failure Rate on {ag.name}",
                        description=f"Success rate dropped to {ag.success_rate_pct:.1f}% across {ag.total_calls} calls.",
                        cta_text="Review Agent",
                        cta_href=f"/workflow/{ag.id}",
                    )
                )
                break  # Alert on highest priority agent

        # Condition 5: Inactive agents
        archived_agents = [
            ag for ag in agent_performance
            if str(ag.status).lower() in ("archived", "workflowstatus.archived")
        ]
        if archived_agents and len(archived_agents) == len(agent_performance):
            attention_items.append(
                AttentionItem(
                    id="all-agents-archived",
                    severity="info",
                    title="All Voice Agents Archived",
                    description="All agents in this workspace are archived. Activate or create a new agent to receive calls.",
                    cta_text="View Agents",
                    cta_href="/workflow",
                )
            )

    # -------------------------------------------------------------------
    # Construct Full Response
    # -------------------------------------------------------------------
    overview_kpi = DashboardOverviewKpi(
        total_calls=curr_total_calls,
        connected_calls=curr_connected_calls,
        successful_calls=curr_successful_calls,
        talk_time_seconds=round(curr_talk_time_seconds, 1),
        success_rate_pct=curr_success_rate,
        connected_rate_pct=curr_connected_rate,
        total_spend_usd=round(curr_total_spend, 2),
        wallet_balance_usd=round(wallet_balance, 2),
        active_calls=active_calls,
        subscription_tier=subscription_tier,
        subscription_status=subscription_status,
        comparison=comparison,
    )

    usage_summary = UsageSummary(
        total_duration_minutes=round(curr_talk_time_seconds / 60.0, 1),
        estimated_spend_usd=round(curr_total_spend, 2),
        monthly_minutes_used=monthly_minutes_used,
        monthly_minutes_limit=float(custom_monthly_minutes) if custom_monthly_minutes else None,
        wallet_balance_usd=round(wallet_balance, 2),
        subscription_tier=subscription_tier,
        active_concurrent_calls=active_calls,
    )

    return DashboardOverviewResponse(
        range_preset=range_preset,
        timezone=timezone,
        period_start=start_utc.isoformat(),
        period_end=end_utc.isoformat(),
        overview=overview_kpi,
        call_activity=activity_points,
        agent_performance=agent_performance,
        campaigns=campaign_items,
        recent_calls=recent_calls,
        usage=usage_summary,
        attention_items=attention_items,
        last_updated=datetime.now(UTC).isoformat(),
    )
