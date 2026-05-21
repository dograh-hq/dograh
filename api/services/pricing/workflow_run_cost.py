from decimal import Decimal

from loguru import logger

from api.db import db_client
from api.enums import WorkflowRunMode
from api.services.pricing.cost_calculator import cost_calculator
from api.services.telephony.factory import get_telephony_provider_for_run


async def _fetch_telephony_cost(workflow_run) -> dict | None:
    """Fetch telephony call cost. Returns a dict with cost_usd and provider_name, or None."""
    if (
        workflow_run.mode
        not in [WorkflowRunMode.TWILIO.value, WorkflowRunMode.VONAGE.value]
        or not workflow_run.cost_info
    ):
        return None

    call_id = workflow_run.cost_info.get("call_id")
    if not call_id:
        logger.warning(f"call_id not found in cost_info")
        return None

    provider_name = workflow_run.mode.lower() if workflow_run.mode else ""

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.warning("Workflow not found for workflow run")
        raise Exception("Workflow not found")

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )
    call_cost_info = await provider.get_call_cost(call_id)

    if call_cost_info.get("status") == "error":
        logger.error(
            f"Failed to fetch {provider_name} call cost: {call_cost_info.get('error')}"
        )
        return None

    cost_usd = call_cost_info.get("cost_usd", 0.0)
    logger.info(
        f"{provider_name.title()} call cost: ${cost_usd:.6f} USD for call {call_id}"
    )
    return {"cost_usd": cost_usd, "provider_name": provider_name}


async def _update_organization_usage(
    org, dograh_tokens: float, duration_seconds: float, charge_usd: float | None
) -> None:
    """Update organization usage after a workflow run."""
    org_id = org.id
    await db_client.update_usage_after_run(
        org_id, dograh_tokens, duration_seconds, charge_usd
    )
    if charge_usd is not None:
        logger.info(
            f"Updated organization usage with ${charge_usd:.2f} USD ({dograh_tokens} Dograh Tokens) and {duration_seconds}s duration for org {org_id}"
        )
    else:
        logger.info(
            f"Updated organization usage with {dograh_tokens} Dograh Tokens and {duration_seconds}s duration for org {org_id}"
        )


async def _get_pricing_organization(workflow_run):
    workflow = getattr(workflow_run, "workflow", None)
    organization_id = getattr(workflow, "organization_id", None)
    if organization_id is None and workflow and workflow.user:
        organization_id = workflow.user.selected_organization_id
    if organization_id is None:
        return None
    return await db_client.get_organization_by_id(organization_id)


async def _build_usage_cost_snapshot(
    usage_info: dict | None,
    *,
    workflow_run=None,
    include_telephony_cost: bool = False,
    organization=None,
    calculated_at: str | None = None,
) -> dict | None:
    if not usage_info:
        logger.warning("No usage info available for workflow run")
        return None

    cost_breakdown = cost_calculator.calculate_total_cost(usage_info)

    if include_telephony_cost and workflow_run is not None:
        try:
            telephony_cost = await _fetch_telephony_cost(workflow_run)
            if telephony_cost:
                telephony_cost_usd = telephony_cost["cost_usd"]
                provider_name = telephony_cost["provider_name"]
                cost_breakdown["telephony_call"] = telephony_cost_usd
                cost_breakdown[f"{provider_name}_call"] = telephony_cost_usd
                cost_breakdown["total"] = (
                    float(cost_breakdown["total"]) + telephony_cost_usd
                )
        except Exception as e:
            logger.error(f"Failed to fetch telephony call cost: {e}")
            # Don't fail the whole cost calculation if telephony API fails

    total_cost_usd = Decimal(str(cost_breakdown["total"]))
    dograh_tokens = float(total_cost_usd * Decimal("100"))

    if organization is None and workflow_run is not None:
        organization = await _get_pricing_organization(workflow_run)

    charge_usd = None
    if organization and organization.price_per_second_usd:
        duration_seconds = usage_info.get("call_duration_seconds", 0)
        charge_usd = float(
            Decimal(str(duration_seconds))
            * Decimal(str(organization.price_per_second_usd))
        )

    cost_info = {
        "cost_breakdown": cost_breakdown,
        "total_cost_usd": float(total_cost_usd),
        "dograh_token_usage": dograh_tokens,
        "calculated_at": calculated_at
        or (workflow_run.created_at.isoformat() if workflow_run is not None else None),
        "call_duration_seconds": usage_info.get("call_duration_seconds", 0),
    }

    if charge_usd is not None:
        cost_info["charge_usd"] = charge_usd
        cost_info["price_per_second_usd"] = organization.price_per_second_usd

    return cost_info


async def build_workflow_run_cost_info(workflow_run) -> dict | None:
    cost_info = await _build_usage_cost_snapshot(
        workflow_run.usage_info,
        workflow_run=workflow_run,
        include_telephony_cost=True,
        calculated_at=workflow_run.created_at.isoformat(),
    )
    if cost_info is None:
        return None
    return {
        **(workflow_run.cost_info or {}),
        **cost_info,
    }


async def save_workflow_run_cost_info(
    workflow_run_id: int, cost_info: dict | None
) -> None:
    if cost_info is None:
        return
    await db_client.update_workflow_run(run_id=workflow_run_id, cost_info=cost_info)


async def apply_workflow_run_usage_to_organization(
    workflow_run, cost_info: dict | None
) -> None:
    if cost_info is None:
        return

    org = await _get_pricing_organization(workflow_run)
    if not org:
        return

    await _update_organization_usage(
        org,
        float(cost_info.get("dograh_token_usage") or 0),
        float(cost_info.get("call_duration_seconds") or 0),
        cost_info.get("charge_usd"),
    )


async def apply_usage_delta_to_organization(
    workflow_run, usage_info: dict | None
) -> dict | None:
    org = await _get_pricing_organization(workflow_run)
    if not org:
        return None

    cost_info = await _build_usage_cost_snapshot(usage_info, organization=org)
    if cost_info is None:
        return None

    await _update_organization_usage(
        org,
        float(cost_info.get("dograh_token_usage") or 0),
        float(cost_info.get("call_duration_seconds") or 0),
        cost_info.get("charge_usd"),
    )
    return cost_info


async def calculate_workflow_run_cost(workflow_run_id: int):
    logger.debug("Calculating cost for workflow run")

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning("Workflow run not found")
        return

    try:
        cost_info = await build_workflow_run_cost_info(workflow_run)
        if cost_info is None:
            return

        await save_workflow_run_cost_info(workflow_run_id, cost_info)

        try:
            await apply_workflow_run_usage_to_organization(workflow_run, cost_info)
        except Exception as e:
            org = await _get_pricing_organization(workflow_run)
            if org:
                logger.error(
                    f"Failed to update organization usage for org {org.id}: {e}"
                )
            else:
                logger.error(f"Failed to update organization usage: {e}")
            # Don't fail the whole cost calculation if usage update fails

        logger.info(
            f"Calculated cost for workflow run: ${cost_info['total_cost_usd']:.6f} USD ({cost_info['dograh_token_usage']} Dograh Tokens)"
        )
    except Exception as e:
        logger.error(f"Error calculating cost for workflow run: {e}")
        raise
