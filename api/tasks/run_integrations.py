"""Execute webhook integrations after workflow run completion."""

from typing import Any, Dict

import httpx
from loguru import logger

from api.db import db_client
from api.db.models import WorkflowRunModel
from api.utils.credential_auth import build_auth_header
from api.utils.template_renderer import render_template
from pipecat.utils.context import set_current_run_id


async def run_integrations_post_workflow_run(_ctx, workflow_run_id: int):
    """
    Run webhook integrations after a workflow run completes.

    This function:
    1. Gets the workflow run and its contexts
    2. Extracts webhook nodes from workflow definition
    3. Executes each enabled webhook node
    """
    set_current_run_id(workflow_run_id)
    logger.info("Running webhook integrations for workflow run")

    try:
        # Step 1: Get workflow run with full context
        workflow_run, organization_id = await db_client.get_workflow_run_with_context(
            workflow_run_id
        )

        if not workflow_run or not workflow_run.workflow:
            logger.error("Workflow run or workflow not found")
            return

        if not organization_id:
            logger.warning("No organization found, skipping webhooks")
            return

        # Step 2: Get workflow definition
        workflow_definition = workflow_run.workflow.workflow_definition_with_fallback
        if not workflow_definition:
            logger.debug("No workflow definition, skipping webhooks")
            return

        # Step 3: Extract webhook nodes
        nodes = workflow_definition.get("nodes", [])
        webhook_nodes = [n for n in nodes if n.get("type") == "webhook"]

        if not webhook_nodes:
            logger.debug("No webhook nodes in workflow")
            return

        logger.info(f"Found {len(webhook_nodes)} webhook nodes to execute")

        # Step 4: Build render context
        render_context = _build_render_context(workflow_run)

        # Step 5: Execute each webhook node
        for node in webhook_nodes:
            webhook_data = node.get("data", {})
            try:
                await _execute_webhook_node(
                    webhook_data=webhook_data,
                    render_context=render_context,
                    organization_id=organization_id,
                )
            except Exception as e:
                # Log error but continue with other webhooks
                logger.error(
                    f"Failed to execute webhook '{webhook_data.get('name', 'unknown')}': {e}"
                )

    except Exception as e:
        logger.error(f"Error running webhook integrations: {e}", exc_info=True)
        raise


def _build_render_context(workflow_run: WorkflowRunModel) -> Dict[str, Any]:
    """Build the context dict for template rendering."""
    return {
        # Top-level fields
        "workflow_run_id": workflow_run.id,
        "workflow_run_name": workflow_run.name,
        "workflow_id": workflow_run.workflow_id,
        "workflow_name": workflow_run.workflow.name if workflow_run.workflow else None,
        # Nested contexts
        "initial_context": workflow_run.initial_context or {},
        "gathered_context": workflow_run.gathered_context or {},
        "cost_info": workflow_run.usage_info or {},
        "recording_url": getattr(workflow_run, "recording_url", None),
        "transcript_url": getattr(workflow_run, "transcript_url", None),
    }


async def _execute_webhook_node(
    webhook_data: Dict[str, Any],
    render_context: Dict[str, Any],
    organization_id: int,
) -> bool:
    """
    Execute a single webhook node.

    Args:
        webhook_data: The webhook node's data dict from workflow definition
        render_context: Context for template rendering
        organization_id: For credential lookup

    Returns:
        True if successful, False otherwise
    """
    webhook_name = webhook_data.get("name", "Unnamed Webhook")

    # 1. Check if enabled
    if not webhook_data.get("enabled", True):
        logger.debug(f"Webhook '{webhook_name}' is disabled, skipping")
        return True

    # 2. Validate endpoint URL
    url = webhook_data.get("endpoint_url")
    if not url:
        logger.error(f"Webhook '{webhook_name}' has no endpoint URL")
        return False

    # 3. Build headers
    headers = {"Content-Type": "application/json"}

    # 4. Add auth header if credential configured
    credential_uuid = webhook_data.get("credential_uuid")
    if credential_uuid:
        credential = await db_client.get_credential_by_uuid(
            credential_uuid, organization_id
        )
        if credential:
            auth_header = build_auth_header(credential)
            headers.update(auth_header)
            logger.debug(f"Applied credential '{credential.name}' to webhook")
        else:
            logger.warning(
                f"Credential {credential_uuid} not found for webhook '{webhook_name}'"
            )

    # 5. Add custom headers
    custom_headers = webhook_data.get("custom_headers", [])
    for h in custom_headers:
        if h.get("key") and h.get("value"):
            headers[h["key"]] = h["value"]

    # 6. Render payload template
    payload_template = webhook_data.get("payload_template", {})
    payload = render_template(payload_template, render_context)

    # 7. Make HTTP request
    method = webhook_data.get("http_method", "POST").upper()

    logger.info(f"Executing webhook '{webhook_name}': {method}")

    try:
        async with httpx.AsyncClient() as client:
            if method in ("POST", "PUT", "PATCH"):
                response = await client.request(
                    method=method,
                    url=url,
                    json=payload,
                    headers=headers,
                    timeout=30.0,
                )
            else:  # GET, DELETE
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    timeout=30.0,
                )

            response.raise_for_status()
            logger.info(f"Webhook '{webhook_name}' succeeded: {response.status_code}")
            return True

    except httpx.HTTPStatusError as e:
        logger.error(
            f"Webhook '{webhook_name}' failed: {e.response.status_code} - {e.response.text[:200]}"
        )
        return False
    except httpx.RequestError as e:
        logger.error(f"Webhook '{webhook_name}' request error: {e}")
        return False
    except Exception as e:
        logger.error(f"Webhook '{webhook_name}' unexpected error: {e}")
        return False
