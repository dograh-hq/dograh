"""Execute integrations (QA analysis, webhooks) after workflow run completion."""

import random
from typing import Any, Dict, Optional

import httpx
from loguru import logger

from api.constants import BACKEND_API_ENDPOINT
from api.db import db_client
from api.db.models import WorkflowRunModel
from api.enums import OrganizationConfigurationKey
from api.services.pipecat.tracing_config import register_org_langfuse_credentials
from api.services.workflow.qa import run_per_node_qa_analysis
from api.utils.credential_auth import build_auth_header
from api.utils.template_renderer import render_template
from pipecat.utils.enums import EndTaskReason
from pipecat.utils.run_context import set_current_org_id, set_current_run_id


def _should_skip_qa(
    node_data: dict,
    workflow_run: WorkflowRunModel,
) -> str | None:
    """Check whether QA analysis should be skipped for this call.

    Returns a reason string if the call should be skipped, or None if it should proceed.
    """
    # Check minimum call duration
    min_duration = node_data.get("qa_min_call_duration", 15)
    usage_info = workflow_run.usage_info or {}
    call_duration = usage_info.get("call_duration_seconds")
    if call_duration is not None and call_duration < min_duration:
        return f"call duration ({call_duration:.1f}s) below minimum ({min_duration}s)"

    # Check voicemail calls
    qa_voicemail_calls = node_data.get("qa_voicemail_calls", False)
    if not qa_voicemail_calls:
        gathered_context = workflow_run.gathered_context or {}
        call_disposition = gathered_context.get("call_disposition", "")
        if call_disposition == EndTaskReason.VOICEMAIL_DETECTED.value:
            return "voicemail call and QA voicemail calls is disabled"

    # Check sample rate
    sample_rate = node_data.get("qa_sample_rate", 100)
    if sample_rate < 100:
        roll = random.randint(1, 100)
        if roll > sample_rate:
            return f"excluded by sampling ({sample_rate}% sample rate, rolled {roll})"

    return None


async def _run_qa_nodes(
    qa_nodes: list[dict],
    workflow_run: WorkflowRunModel,
    workflow_run_id: int,
    workflow_definition: dict,
    definition_id: int | None,
) -> Dict[str, Any]:
    """Run QA analysis for each enabled QA node and aggregate results.

    Returns:
        Dict keyed by node ID with QA analysis results.
    """
    results: Dict[str, Any] = {}

    for node in qa_nodes:
        node_data = node.get("data", {})
        node_id = node.get("id", "unknown")
        node_name = node_data.get("name", "QA Analysis")

        if not node_data.get("qa_enabled", True):
            logger.debug(f"QA node '{node_name}' is disabled, skipping")
            continue

        skip_reason = _should_skip_qa(node_data, workflow_run)
        if skip_reason:
            logger.info(f"Skipping QA node '{node_name}' (#{node_id}): {skip_reason}")
            results[f"qa_{node_id}"] = {"skipped": True, "reason": skip_reason}
            continue

        try:
            logger.info(f"Running QA analysis for node '{node_name}' (#{node_id})")
            result = await run_per_node_qa_analysis(
                node_data,
                workflow_run,
                workflow_run_id,
                workflow_definition,
                definition_id,
            )
            results[f"qa_{node_id}"] = result
            # Log summary from node_results
            node_results = result.get("node_results", {})
            logger.info(
                f"QA analysis complete for '{node_name}': "
                f"{len(node_results)} nodes analyzed"
            )
        except Exception as e:
            logger.error(f"QA analysis failed for node '{node_name}': {e}")
            results[f"qa_{node_id}"] = {"error": str(e)}

    return results


async def _update_usage_info_with_qa_tokens(
    workflow_run_id: int,
    workflow_run: WorkflowRunModel,
    qa_results: Dict[str, Any],
) -> None:
    """Add QA analysis LLM token usage to the workflow run's usage_info."""
    try:
        usage_info = dict(workflow_run.usage_info or {})
        llm_usage = dict(usage_info.get("llm", {}))

        for _node_key, result in qa_results.items():
            token_usage = result.get("token_usage")
            model = result.get("model")
            if not token_usage or not model:
                continue

            key = f"QAAnalysis|||{model}"
            if key in llm_usage:
                # Aggregate if multiple QA nodes use the same model
                existing = llm_usage[key]
                for field in (
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "cache_read_input_tokens",
                ):
                    existing[field] = (existing.get(field) or 0) + (
                        token_usage.get(field) or 0
                    )
            else:
                llm_usage[key] = token_usage

        usage_info["llm"] = llm_usage
        await db_client.update_workflow_run(
            run_id=workflow_run_id, usage_info=usage_info
        )
        logger.info(f"Updated usage_info with QA token usage for run {workflow_run_id}")
    except Exception as e:
        logger.error(f"Failed to update usage_info with QA tokens: {e}")


async def run_integrations_post_workflow_run(_ctx, workflow_run_id: int):
    """
    Run integrations after a workflow run completes.

    This function:
    1. Gets the workflow run and its contexts
    2. Runs QA analysis nodes (if any)
    3. Stores QA results in annotations
    4. Executes webhook nodes with QA results available in render context
    """
    set_current_run_id(workflow_run_id)
    logger.info("Running integrations for workflow run")

    try:
        # Step 1: Get workflow run with full context
        workflow_run, organization_id = await db_client.get_workflow_run_with_context(
            workflow_run_id
        )

        if not workflow_run or not workflow_run.workflow:
            logger.warning("Workflow run or workflow not found")
            return

        if not organization_id:
            logger.warning("No organization found, skipping integrations")
            return

        # Set org context for tracing and register org-specific Langfuse credentials
        # FIXME: If an org removes langfuse credentials during an exisitng deployment
        # we should unregister an existing langfuse credentials for that org.
        set_current_org_id(organization_id)
        langfuse_config = await db_client.get_configuration_value(
            organization_id,
            OrganizationConfigurationKey.LANGFUSE_CREDENTIALS.value,
        )
        if langfuse_config:
            register_org_langfuse_credentials(
                org_id=organization_id,
                host=langfuse_config.get("host"),
                public_key=langfuse_config.get("public_key"),
                secret_key=langfuse_config.get("secret_key"),
            )

        # Step 2: Get workflow definition from the run's pinned version
        workflow_definition = workflow_run.definition.workflow_json
        definition_id = workflow_run.definition.id

        if not workflow_definition:
            logger.debug("No workflow definition, skipping integrations")
            return

        # Step 3: Extract integration nodes
        nodes = workflow_definition.get("nodes", [])
        qa_nodes = [n for n in nodes if n.get("type") == "qa"]
        webhook_nodes = [n for n in nodes if n.get("type") == "webhook"]

        # Step 4: Generate public access token if webhooks exist or campaign_id is set
        has_campaign = workflow_run.campaign_id is not None
        if not webhook_nodes and not qa_nodes and not has_campaign:
            logger.debug("No integration nodes and no campaign, skipping")
            return

        public_token = None
        if webhook_nodes or has_campaign:
            public_token = await db_client.ensure_public_access_token(workflow_run_id)

        # Step 5: Run QA analysis before webhooks
        if qa_nodes:
            logger.info(f"Found {len(qa_nodes)} QA nodes to execute")
            qa_results = await _run_qa_nodes(
                qa_nodes,
                workflow_run,
                workflow_run_id,
                workflow_definition,
                definition_id,
            )

            if qa_results:
                # Add QA token usage to workflow run's usage_info
                await _update_usage_info_with_qa_tokens(
                    workflow_run_id, workflow_run, qa_results
                )

                # Collect unique tags across all QA node results for top-level filtering
                all_tags: set[str] = set()
                for qa_key, qa_result in qa_results.items():
                    for node_result in qa_result.get("node_results", {}).values():
                        for tag in node_result.get("tags", []):
                            if isinstance(tag, str):
                                all_tags.add(tag)
                            elif isinstance(tag, dict) and "tag" in tag:
                                all_tags.add(tag["tag"])
                if all_tags:
                    qa_results["tags"] = sorted(all_tags)

                await db_client.update_workflow_run(
                    workflow_run_id, annotations=qa_results
                )

                # Re-fetch workflow_run to get updated annotations
                workflow_run, _ = await db_client.get_workflow_run_with_context(
                    workflow_run_id
                )

        # Step 6: Execute webhooks
        if not webhook_nodes:
            logger.debug("No webhook nodes in workflow")
            return

        logger.info(f"Found {len(webhook_nodes)} webhook nodes to execute")

        # Step 7: Build render context (includes annotations from QA)
        render_context = _build_render_context(workflow_run, public_token)

        # Step 8: Execute each webhook node
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
                logger.warning(
                    f"Failed to execute webhook '{webhook_data.get('name', 'unknown')}': {e}"
                )

    except Exception as e:
        logger.error(f"Error running integrations: {e}", exc_info=True)
        raise


def _build_render_context(
    workflow_run: WorkflowRunModel, public_token: Optional[str] = None
) -> Dict[str, Any]:
    """Build the context dict for template rendering.

    Args:
        workflow_run: The workflow run model
        public_token: Optional public access token for download URLs

    Returns:
        Dict containing all fields available for template rendering
    """
    context = {
        # Top-level fields
        "workflow_run_id": workflow_run.id,
        "workflow_run_name": workflow_run.name,
        "workflow_id": workflow_run.workflow_id,
        "workflow_name": workflow_run.workflow.name if workflow_run.workflow else None,
        # Nested contexts
        "initial_context": workflow_run.initial_context or {},
        "gathered_context": workflow_run.gathered_context or {},
        "cost_info": workflow_run.usage_info or {},
        # Annotations (includes QA results)
        "annotations": workflow_run.annotations or {},
    }

    # Add public download URLs if token is available
    if public_token:
        base_url = (
            f"{BACKEND_API_ENDPOINT}/api/v1/public/download/workflow/{public_token}"
        )
        context["recording_url"] = (
            f"{base_url}/recording" if workflow_run.recording_url else None
        )
        context["transcript_url"] = (
            f"{base_url}/transcript" if workflow_run.transcript_url else None
        )
    else:
        context["recording_url"] = workflow_run.recording_url
        context["transcript_url"] = workflow_run.transcript_url

    return context


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
        logger.warning(f"Webhook '{webhook_name}' has no endpoint URL")
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
        logger.warning(
            f"Webhook '{webhook_name}' failed: {e.response.status_code} - {e.response.text[:200]}"
        )
        return False
    except httpx.RequestError as e:
        logger.warning(f"Webhook '{webhook_name}' request error: {e}")
        return False
    except Exception as e:
        logger.error(f"Webhook '{webhook_name}' unexpected error: {e}")
        return False
