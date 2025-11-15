import os

import aiohttp
import httpx
from loguru import logger

from api.db import db_client
from api.db.models import IntegrationModel
from api.enums import OrganizationConfigurationKey, WorkflowRunMode
from api.utils.template_renderer import render_template
from pipecat.utils.context import set_current_run_id


async def run_integrations_post_workflow_run(ctx, workflow_run_id: int):
    """
    Run integrations after a workflow run completes.

    This function:
    1. Gets the workflow run and its gathered_context
    2. Determines the organization_id through the workflow -> user -> organization chain
    3. Fetches all active integrations for that organization
    4. For Slack integrations, sends the gathered_context to the webhook URL

    Args:
        workflow_run_id: The ID of the completed workflow run
    """
    # Set the workflow_run_id in context variable for consistent logging format
    set_current_run_id(workflow_run_id)
    logger.info("Running integrations for workflow run")

    try:
        # Step 1: Get workflow run details with gathered_context using DB client
        workflow_run, organization_id = await db_client.get_workflow_run_with_context(
            workflow_run_id
        )

        if not workflow_run:
            logger.error("Workflow run not found")
            return

        if not workflow_run.workflow:
            logger.error("Workflow not found for workflow run")
            return

        if not workflow_run.workflow.user:
            logger.error("User not found for workflow run")
            return

        gathered_context = workflow_run.gathered_context
        initial_context = workflow_run.initial_context

        if not gathered_context:
            logger.info("No gathered context for workflow run, skipping integrations")
            return

        # Check if workflow run mode is stasis and sync with vendor
        if workflow_run.mode == WorkflowRunMode.STASIS.value:
            await _sync_vendor_data(initial_context, gathered_context)

        # Step 2: Check if organization_id is available
        if not organization_id:
            logger.warning(
                f"No organization found for workflow run, skipping integrations"
            )
            return

        logger.debug(f"Found organization_id {organization_id} for workflow run")

        # Step 3: Get all active integrations for the organization using DB client
        integrations = await db_client.get_active_integrations_by_organization(
            organization_id
        )

        logger.info(
            f"Found {len(integrations)} active integrations for organization {organization_id}"
        )

        # Step 4: Process each integration
        for integration in integrations:
            await _process_integration(integration, gathered_context)

    except Exception as e:
        logger.error(f"Error running integrations for workflow run: {str(e)}")
        raise


async def _sync_vendor_data(initial_context: dict, gathered_context: dict):
    """
    Sync data with external vendor for stasis mode workflow runs.

    Args:
        initial_context: The initial context containing lead_id
        gathered_context: The gathered context containing mapped_call_disposition
    """
    if not os.getenv("ARI_DATA_SYNCING_URI"):
        logger.info("ARI_DATA_SYNCING_URI not configured, skipping vendor sync")
        return

    try:
        lead_id = initial_context.get("lead_id")
        status = gathered_context.get("mapped_call_disposition")

        if lead_id and status:
            ari_data_uri = os.getenv("ARI_DATA_SYNCING_URI")
            # Add URL params to the base URL
            sync_url = f"{ari_data_uri}&lead_id={lead_id}&status={status}"

            async with httpx.AsyncClient() as client:
                response = await client.post(sync_url, timeout=10.0)
                response.raise_for_status()
                logger.info(
                    f"Successfully synced data for lead_id: {lead_id} with status: {status}"
                )
        else:
            logger.warning(
                f"Missing lead_id or status for syncing - lead_id: {lead_id}, status: {status}"
            )
    except Exception as e:
        logger.error(f"Failed to sync data to ARI_DATA_SYNCING_URI: {e}")


async def _process_integration(
    integration: IntegrationModel,
    gathered_context: dict,
):
    """
    Process a single integration.

    Args:
        integration: The integration model
        gathered_context: The gathered context from the workflow run
        workflow_run_name: Name of the workflow run
        run_id: The workflow run ID for logging context
    """
    logger.info(
        f"Processing integration {integration.id} (provider: {integration.provider})"
    )

    try:
        if integration.provider.lower() == "slack":
            await _process_slack_integration(integration, gathered_context)
        else:
            logger.info(
                f"Integration provider '{integration.provider}' not supported yet"
            )

    except Exception as e:
        logger.error(f"Error processing integration {integration.id}: {str(e)}")


async def _process_slack_integration(
    integration: IntegrationModel, gathered_context: dict
):
    """
    Process a Slack integration by sending gathered_context to the webhook.

    Args:
        integration: The Slack integration model
        gathered_context: The gathered context from the workflow run
        workflow_run_name: Name of the workflow run
        run_id: The workflow run ID for logging context
    """
    logger.info(f"Processing Slack integration {integration.id}")

    # TODO: Generalise this
    if gathered_context.get("mapped_call_disposition") != "XFER":
        logger.debug(
            f"Not sending message on slack since not XFER: {gathered_context.get('mapped_call_disposition')}"
        )
        return

    try:
        # Extract webhook URL from connection_details
        connection_details = integration.connection_details

        if not connection_details:
            logger.error(
                f"No connection details found for Slack integration {integration.id}"
            )
            return

        # Navigate to incoming_webhook.url in the connection_details
        webhook_url = connection_details.get("connection_config", {}).get(
            "incoming_webhook.url"
        )
        if not webhook_url:
            logger.error(
                f"No incoming_webhook found in connection details for integration {integration.id}"
            )
            return

        logger.info(f"Found Slack webhook URL for integration {integration.id}")

        # Get message template configuration
        # Get organization_id from the integration model
        organization_id = integration.organisation_id
        message_templates = await db_client.get_configuration_value(
            organization_id,
            OrganizationConfigurationKey.DISPOSITION_MESSAGE_TEMPLATE.value,
            default={},
        )

        # Check if there's a custom template for Slack
        slack_template = message_templates.get("slack", {})
        rendered_text = render_template(slack_template, gathered_context)

        slack_message = {"text": rendered_text}

        # Send to Slack webhook
        async with aiohttp.ClientSession() as session:
            async with session.post(
                webhook_url,
                json=slack_message,
                headers={"Content-Type": "application/json"},
            ) as response:
                if response.status == 200:
                    logger.info(
                        f"Successfully sent message to Slack for integration {integration.id}"
                    )
                else:
                    error_text = await response.text()
                    logger.error(
                        f"Failed to send Slack message for integration {integration.id}: {response.status} - {error_text}"
                    )

    except Exception as e:
        logger.error(f"Error processing Slack integration {integration.id}: {str(e)}")
