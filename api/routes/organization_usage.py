import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from api.constants import DEPLOYMENT_MODE, UI_APP_URL
from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user, get_user_with_selected_organization
from api.services.mps_service_key_client import mps_service_key_client
from api.services.reports import generate_usage_runs_report_csv
from api.utils.artifacts import artifact_url
from api.utils.recording_artifacts import has_recording_track

router = APIRouter(prefix="/organizations")


class CurrentUsageResponse(BaseModel):
    period_start: str
    period_end: str
    used_dograh_tokens: float
    total_duration_seconds: int
    used_amount_usd: Optional[float] = None
    currency: Optional[str] = None
    price_per_second_usd: Optional[float] = None


class MPSCreditsResponse(BaseModel):
    total_credits_used: float
    remaining_credits: float
    total_quota: float


class MPSCreditPurchaseUrlResponse(BaseModel):
    checkout_url: str


class MPSBillingAccountResponse(BaseModel):
    id: int
    organization_id: int
    billing_mode: str
    cached_balance_credits: float
    currency: str


class MPSCreditLedgerEntryResponse(BaseModel):
    id: int
    entry_type: str
    origin: Optional[str] = None
    credits_delta: float
    balance_after: float
    amount_minor: Optional[int] = None
    amount_currency: Optional[str] = None
    payment_order_id: Optional[int] = None
    metric_code: Optional[str] = None
    correlation_id: Optional[str] = None
    aggregation_key: Optional[str] = None
    usage_event_id: Optional[int] = None
    workflow_run_id: Optional[int] = None
    workflow_id: Optional[int] = None
    billable_quantity: Optional[float] = None
    quantity_unit: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class MPSBillingCreditsResponse(BaseModel):
    billing_version: Literal["legacy", "v2"]
    total_credits_used: float = 0.0
    remaining_credits: float = 0.0
    total_quota: float = 0.0
    account: Optional[MPSBillingAccountResponse] = None
    ledger_entries: List[MPSCreditLedgerEntryResponse] = Field(default_factory=list)
    total_count: int = 0
    page: int = 1
    limit: int = 50
    total_pages: int = 0


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
    user_recording_url: Optional[str] = None
    bot_recording_url: Optional[str] = None
    recording_public_url: Optional[str] = None
    transcript_public_url: Optional[str] = None
    user_recording_public_url: Optional[str] = None
    bot_recording_public_url: Optional[str] = None
    public_access_token: Optional[str] = None
    phone_number: Optional[str] = Field(
        default=None,
        deprecated=True,
        description="Deprecated. Use caller_number and called_number instead.",
    )
    caller_number: Optional[str] = None
    called_number: Optional[str] = None
    call_type: Optional[str] = None
    mode: Optional[str] = None
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
    """Get current reporting-period usage for the user's organization."""
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


async def _get_mps_billing_account_status(
    user: UserModel, organization_id: int
) -> Optional[dict]:
    return await mps_service_key_client.get_billing_account_status(
        organization_id=organization_id,
        created_by=str(user.provider_id),
    )


def _is_mps_billing_v2(account: Optional[dict]) -> bool:
    return bool(account and account.get("billing_mode") == "v2")


async def _legacy_mps_credits_response(user: UserModel) -> MPSBillingCreditsResponse:
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

    total_used = float(usage.get("total_credits_used", 0.0))
    total_remaining = float(usage.get("remaining_credits", 0.0))
    return MPSBillingCreditsResponse(
        billing_version="legacy",
        total_credits_used=total_used,
        remaining_credits=total_remaining,
        total_quota=total_used + total_remaining,
    )


@router.get("/billing/credits", response_model=MPSBillingCreditsResponse)
async def get_billing_credits(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    user: UserModel = Depends(get_user),
):
    """Return legacy MPS credits or paginated v2 billing ledger details for the org."""
    try:
        if DEPLOYMENT_MODE == "oss" or not user.selected_organization_id:
            return await _legacy_mps_credits_response(user)

        organization_id = user.selected_organization_id
        account_status = await _get_mps_billing_account_status(user, organization_id)
        if not _is_mps_billing_v2(account_status):
            return await _legacy_mps_credits_response(user)

        ledger = await mps_service_key_client.get_credit_ledger(
            organization_id=organization_id,
            page=page,
            limit=limit,
            created_by=str(user.provider_id),
        )
        account = ledger.get("account") or {}
        ledger_entries = ledger.get("ledger_entries") or []
        total_count = int(ledger.get("total_count") or len(ledger_entries))
        response_limit = int(ledger.get("limit") or limit)
        total_pages = int(
            ledger.get("total_pages")
            or ((total_count + response_limit - 1) // response_limit)
        )
        workflow_ids_by_run_id: dict[int, int] = {}
        workflow_run_ids = {
            workflow_run_id
            for entry in ledger_entries
            if (workflow_run_id := _optional_int(entry.get("workflow_run_id")))
            is not None
        }
        for workflow_run_id in workflow_run_ids:
            workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
            if (
                workflow_run
                and workflow_run.workflow
                and workflow_run.workflow.organization_id == organization_id
            ):
                workflow_ids_by_run_id[workflow_run_id] = workflow_run.workflow_id

        balance = float(account.get("cached_balance_credits") or 0.0)
        total_debits = sum(
            abs(float(entry.get("credits_delta") or 0.0))
            for entry in ledger_entries
            if float(entry.get("credits_delta") or 0.0) < 0
        )
        if ledger.get("total_debits_credits") is not None:
            total_debits = float(ledger["total_debits_credits"])

        return MPSBillingCreditsResponse(
            billing_version="v2",
            total_credits_used=total_debits,
            remaining_credits=balance,
            total_quota=balance + total_debits,
            account=MPSBillingAccountResponse(
                id=int(account["id"]),
                organization_id=int(account["organization_id"]),
                billing_mode=str(account["billing_mode"]),
                cached_balance_credits=balance,
                currency=str(account.get("currency") or "USD"),
            ),
            ledger_entries=[
                MPSCreditLedgerEntryResponse(
                    id=int(entry["id"]),
                    entry_type=str(entry["entry_type"]),
                    origin=entry.get("origin"),
                    credits_delta=float(entry.get("credits_delta") or 0.0),
                    balance_after=float(entry.get("balance_after") or 0.0),
                    amount_minor=entry.get("amount_minor"),
                    amount_currency=entry.get("amount_currency"),
                    payment_order_id=entry.get("payment_order_id"),
                    metric_code=entry.get("metric_code"),
                    correlation_id=entry.get("correlation_id"),
                    aggregation_key=entry.get("aggregation_key"),
                    usage_event_id=_optional_int(entry.get("usage_event_id")),
                    workflow_run_id=_optional_int(entry.get("workflow_run_id")),
                    workflow_id=(
                        workflow_ids_by_run_id.get(
                            _optional_int(entry.get("workflow_run_id"))
                        )
                        if entry.get("workflow_run_id") is not None
                        else None
                    ),
                    billable_quantity=(
                        float(entry["billable_quantity"])
                        if entry.get("billable_quantity") is not None
                        else None
                    ),
                    quantity_unit=entry.get("quantity_unit"),
                    metadata=entry.get("metadata") or {},
                    created_at=str(entry["created_at"]),
                )
                for entry in ledger_entries
            ],
            total_count=total_count,
            page=int(ledger.get("page") or page),
            limit=response_limit,
            total_pages=total_pages,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to fetch billing credits: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/usage/mps-credits/purchase-url",
    response_model=MPSCreditPurchaseUrlResponse,
)
async def create_mps_credit_purchase_url(
    user: UserModel = Depends(get_user_with_selected_organization),
):
    """Create a checkout URL for organizations using Dograh-managed MPS v2."""
    if DEPLOYMENT_MODE == "oss":
        raise HTTPException(
            status_code=404,
            detail="Credit purchases are not available in OSS mode",
        )

    organization_id = user.selected_organization_id
    assert organization_id is not None
    account_status = await _get_mps_billing_account_status(user, organization_id)
    if not _is_mps_billing_v2(account_status):
        raise HTTPException(
            status_code=403,
            detail=(
                "Credit purchases are available only for organizations using billing v2"
            ),
        )

    try:
        session = await mps_service_key_client.create_credit_purchase_url(
            organization_id=organization_id,
            created_by=str(user.provider_id),
            return_url=f"{UI_APP_URL.rstrip('/')}/billing",
            billing_details={
                "source": "dograh_billing",
                "dograh_user_id": str(user.id),
                "dograh_provider_id": str(user.provider_id),
            },
        )
    except Exception as exc:
        logger.error(f"Failed to create MPS credit purchase URL: {exc}")
        raise HTTPException(
            status_code=502,
            detail="Failed to create credit purchase URL",
        )

    checkout_url = session.get("checkout_url")
    if not checkout_url:
        logger.error(f"MPS checkout session response missing checkout_url: {session}")
        raise HTTPException(
            status_code=502,
            detail="MPS checkout session response missing checkout_url",
        )
    return MPSCreditPurchaseUrlResponse(checkout_url=checkout_url)


FILTERS_DESCRIPTION = """\
JSON-encoded array of filter objects. Each object has the shape:

```json
{ "attribute": "<name>", "type": "<type>", "value": <value> }
```

Supported `attribute` / `type` / `value` combinations:

| attribute       | type          | value shape                                  | matches                                              |
|-----------------|---------------|----------------------------------------------|------------------------------------------------------|
| `runId`         | `number`      | `{ "value": 12345 }`                         | exact run id                                         |
| `workflowId`    | `number`      | `{ "value": 42 }`                            | exact agent (workflow) id                            |
| `campaignId`    | `number`      | `{ "value": 7 }`                             | exact campaign id                                    |
| `callerNumber`  | `text`        | `{ "value": "415555" }`                      | substring match on `initial_context.caller_number`   |
| `calledNumber`  | `text`        | `{ "value": "9911848" }`                     | substring match on `initial_context.called_number`   |
| `dispositionCode` | `multiSelect` | `{ "codes": ["XFER", "DNC"] }`             | any of the codes in `gathered_context.mapped_call_disposition` |
| `duration`      | `numberRange` | `{ "min": 60, "max": 300 }`                  | call duration (seconds), inclusive bounds            |

Unknown attributes and unsupported `type` values are silently ignored.

Date filtering on this endpoint is done via the dedicated `start_date` / `end_date` query params, not via a `dateRange` filter object.
"""


@router.get("/usage/runs", response_model=UsageHistoryResponse)
async def get_usage_history(
    start_date: Optional[str] = Query(
        None,
        description="ISO 8601 date-time string (UTC). Lower bound (inclusive) on `created_at`.",
        examples=["2026-04-01T00:00:00Z"],
    ),
    end_date: Optional[str] = Query(
        None,
        description="ISO 8601 date-time string (UTC). Upper bound (inclusive) on `created_at`.",
        examples=["2026-05-01T00:00:00Z"],
    ),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    filters: Optional[str] = Query(
        None,
        description=FILTERS_DESCRIPTION,
        examples=[
            '[{"attribute":"callerNumber","type":"text","value":{"value":"415555"}}]',
            '[{"attribute":"campaignId","type":"number","value":{"value":7}},'
            '{"attribute":"duration","type":"numberRange","value":{"min":60,"max":300}}]',
            '[{"attribute":"dispositionCode","type":"multiSelect","value":{"codes":["XFER","DNC"]}}]',
        ],
    ),
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

        for run in runs:
            public_access_token = run.get("public_access_token")
            run["transcript_public_url"] = artifact_url(
                public_access_token, "transcript"
            )
            run["recording_public_url"] = artifact_url(public_access_token, "recording")
            run["user_recording_public_url"] = (
                artifact_url(public_access_token, "user_recording")
                if has_recording_track(run.get("extra"), "user")
                else None
            )
            run["bot_recording_public_url"] = (
                artifact_url(public_access_token, "bot_recording")
                if has_recording_track(run.get("extra"), "bot")
                else None
            )
            run.pop("extra", None)

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


@router.get("/usage/runs/report")
async def download_usage_runs_report(
    start_date: Optional[str] = Query(
        None,
        description="ISO 8601 date-time string (UTC). Lower bound (inclusive) on `created_at`.",
    ),
    end_date: Optional[str] = Query(
        None,
        description="ISO 8601 date-time string (UTC). Upper bound (inclusive) on `created_at`.",
    ),
    filters: Optional[str] = Query(
        None,
        description=FILTERS_DESCRIPTION,
    ),
    user: UserModel = Depends(get_user),
) -> StreamingResponse:
    """Download a CSV of runs matching the same filters as `/usage/runs`."""
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    start_dt = datetime.fromisoformat(start_date) if start_date else None
    end_dt = datetime.fromisoformat(end_date) if end_date else None

    parsed_filters = None
    if filters:
        try:
            parsed_filters = json.loads(filters)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid filters format")

    output, filename = await generate_usage_runs_report_csv(
        user.selected_organization_id,
        start_date=start_dt,
        end_date=end_dt,
        filters=parsed_filters,
    )

    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
