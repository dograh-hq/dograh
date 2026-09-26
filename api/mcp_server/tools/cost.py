"""Pre-flight cost estimate for a call.

Dograh prices call time per second (`OrganizationModel.price_per_second_usd`) and
records what a run actually cost after it finishes. This tool answers the same
question before the call is placed, from the same per-second price the usage
breakdown multiplies by realized seconds, so an estimate and the later invoice
line agree.
"""

from fastapi import HTTPException

from api.db import db_client
from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tracing import traced_tool


@traced_tool
async def estimate_call_cost(expected_duration_seconds: int = 60) -> dict:
    """Estimate what a call of a given length will cost, before placing it.

    Multiplies the organization's `price_per_second_usd` by the expected talk
    time — the same arithmetic the usage breakdown applies to realized seconds.
    Nothing is placed, charged, or recorded.

    Output shape:
        {"expected_duration_seconds": int, "price_per_second_usd": float,
         "estimated_total_usd": float, "currency": "USD"}

    `estimated_total_usd` keeps six decimals so a sub-cent estimate survives;
    invoices round it to cents.
    """
    if expected_duration_seconds < 0:
        raise HTTPException(
            status_code=400, detail="expected_duration_seconds must not be negative"
        )

    user = await authenticate_mcp_request()
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    org = await db_client.get_organization_by_id(user.selected_organization_id)
    if not org or org.price_per_second_usd is None:
        raise HTTPException(
            status_code=400,
            detail="Cost estimates are only available for organizations with pricing configured",
        )

    return {
        "expected_duration_seconds": expected_duration_seconds,
        "price_per_second_usd": org.price_per_second_usd,
        "estimated_total_usd": round(
            expected_duration_seconds * org.price_per_second_usd, 6
        ),
        "currency": "USD",
    }
