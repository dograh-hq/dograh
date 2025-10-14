from loguru import logger

from api.db import db_client
from api.enums import WorkflowRunMode
from api.services.pricing.cost_calculator import cost_calculator
from api.services.telephony.twilio import TwilioService
from pipecat.utils.context import set_current_run_id


async def calculate_workflow_run_cost(ctx, workflow_run_id: int):
    # Set the run_id in context variable for consistent logging format
    set_current_run_id(workflow_run_id)
    logger.debug("Calculating cost for workflow run")

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning("Workflow run not found")
        return

    workflow_usage_info = workflow_run.usage_info
    if not workflow_usage_info:
        logger.warning("No usage info available for workflow run")
        return

    try:
        # Calculate cost breakdown
        cost_breakdown = cost_calculator.calculate_total_cost(workflow_usage_info)

        # If this is a Twilio call, fetch the Twilio call cost
        twilio_cost_usd = 0.0
        if workflow_run.mode == WorkflowRunMode.TWILIO.value and workflow_run.cost_info:
            twilio_call_sid = workflow_run.cost_info.get("twilio_call_sid")
            if twilio_call_sid:
                try:
                    # Get workflow to access organization_id
                    workflow = await db_client.get_workflow_by_id(
                        workflow_run.workflow_id
                    )
                    if not workflow:
                        logger.warning("Workflow not found for workflow run")
                        raise Exception("Workflow not found")

                    # TODO: Migrate to use telephony provider abstraction
                    # provider = await get_telephony_provider(workflow.organization_id)
                    # call_info = await provider.get_call_status(twilio_call_sid)
                    twilio_service = TwilioService(workflow.organization_id)
                    call_info = await twilio_service.get_call(twilio_call_sid)
                    # Twilio returns price as a string with negative value (e.g., "-0.0085")
                    if call_info.get("price"):
                        twilio_cost_usd = abs(float(call_info["price"]))
                        cost_breakdown["twilio_call"] = twilio_cost_usd
                        # Add Twilio cost to the total
                        cost_breakdown["total"] = (
                            float(cost_breakdown["total"]) + twilio_cost_usd
                        )
                        logger.info(
                            f"Twilio call cost: ${twilio_cost_usd:.6f} USD for call {twilio_call_sid}"
                        )
                except Exception as e:
                    logger.error(f"Failed to fetch Twilio call cost: {e}")
                    # Don't fail the whole cost calculation if Twilio API fails

        # Store cost information back to the workflow run
        # We'll add the cost breakdown to the workflow run
        # Convert USD to Dograh Tokens (1 cent = 1 token)
        dograh_tokens = round(float(cost_breakdown["total"]) * 100, 2)

        # Get organization to check if it has USD pricing
        org = None
        charge_usd = None
        if (
            workflow_run.workflow
            and workflow_run.workflow.user
            and workflow_run.workflow.user.selected_organization_id
        ):
            org = await db_client.get_organization_by_id(
                workflow_run.workflow.user.selected_organization_id
            )

        # Calculate USD cost if organization has pricing configured
        if org and org.price_per_second_usd:
            duration_seconds = workflow_usage_info.get("call_duration_seconds", 0)
            charge_usd = duration_seconds * org.price_per_second_usd

        cost_info = {
            "cost_breakdown": cost_breakdown,
            "total_cost_usd": float(cost_breakdown["total"]),
            "dograh_token_usage": dograh_tokens,
            "calculated_at": workflow_run.created_at.isoformat(),
            "call_duration_seconds": workflow_usage_info["call_duration_seconds"],
        }

        # Add USD cost if available
        if charge_usd is not None:
            cost_info["charge_usd"] = charge_usd
            cost_info["price_per_second_usd"] = org.price_per_second_usd

        # Preserve the twilio_call_sid if it exists
        if workflow_run.cost_info and "twilio_call_sid" in workflow_run.cost_info:
            cost_info["twilio_call_sid"] = workflow_run.cost_info["twilio_call_sid"]

        # Update workflow run with cost information
        await db_client.update_workflow_run(run_id=workflow_run_id, cost_info=cost_info)

        # Update organization usage if applicable
        if org:
            org_id = org.id
            try:
                duration_seconds = workflow_usage_info.get("call_duration_seconds", 0)
                # Pass USD amount if organization has pricing
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
            except Exception as e:
                logger.error(
                    f"Failed to update organization usage for org {org_id}: {e}"
                )
                # Don't fail the whole task if usage update fails

        logger.info(
            f"Calculated cost for workflow run: ${cost_breakdown['total']:.6f} USD ({dograh_tokens} Dograh Tokens)"
        )

    except Exception as e:
        logger.error(f"Error calculating cost for workflow run: {e}")
        raise
