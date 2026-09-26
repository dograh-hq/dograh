from fastapi import HTTPException

from api.db import db_client
from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tracing import traced_tool


@traced_tool
async def estimate_call_cost(
    workflow_id: int,
    expected_duration_seconds: int = 60,
) -> dict:
    """Estimate what a call on this agent will cost, before the call runs.

    Dograh bills per second of call time, and a run's `cost_info` is only
    written once the run completes. This is the same arithmetic applied
    up front, so a caller can budget or decide whether to place a call
    instead of discovering the charge afterwards.

    `expected_duration_seconds` is the caller's own estimate of how long
    the call will last (default 60). The result is an estimate, not a
    quote: the charge is computed from the call's real duration.

    Output shape:
        {"workflow_id": int, "expected_duration_seconds": int,
         "price_per_second_usd": float, "estimated_total_usd": float,
         "currency": "USD", "source": "price_per_second_usd"}

    Only available for organizations with pricing configured.
    """
    if expected_duration_seconds <= 0:
        raise HTTPException(
            status_code=400,
            detail="expected_duration_seconds must be greater than 0",
        )

    user = await authenticate_mcp_request()

    workflow = await db_client.get_workflow(
        workflow_id, organization_id=user.selected_organization_id
    )
    if not workflow:
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")

    org = await db_client.get_organization_by_id(user.selected_organization_id)
    if not org or org.price_per_second_usd is None:
        raise HTTPException(
            status_code=400,
            detail="Cost estimates are only available for organizations with pricing configured",
        )

    # Same formula the usage breakdown bills with (seconds * price_per_second_usd).
    # Rounded to 6dp rather than the 2dp used for monthly aggregates: a short call
    # costs a fraction of a cent, and 2dp would report it as 0.00.
    estimated_total_usd = round(expected_duration_seconds * org.price_per_second_usd, 6)

    return {
        "workflow_id": workflow_id,
        "expected_duration_seconds": expected_duration_seconds,
        "price_per_second_usd": org.price_per_second_usd,
        "estimated_total_usd": estimated_total_usd,
        "currency": "USD",
        "source": "price_per_second_usd",
    }
