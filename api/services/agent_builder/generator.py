"""Agent-builder orchestration.

Pipeline: build inputs -> call Claude (anthropic_client) -> validate the
returned definition against the workflow DTO -> create http_api tools and wire
their uuids onto the matching nodes -> ensure a webhook node + tool when an
order webhook URL was provided -> create the workflow and a draft carrying the
model config -> validate the graph -> return {workflow_id, status, name,
warnings, editor_path}.

All workflow/tool work goes through the in-process service/DB layer (no HTTP
self-calls), scoped to the caller's organization.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import ValidationError

from api.db import db_client
from api.db.agent_trigger_client import TriggerPathConflictError
from api.db.models import UserModel
from api.schemas.tool import CreateToolRequest
from api.services.agent_builder import anthropic_client
from api.services.agent_builder.templates.gpc_retail import DEFAULT_MODEL_CONFIG
from api.services.configuration.ai_model_configuration import (
    WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY,
)
from api.services.tool_management import ToolManagementError, create_tool_for_user
from api.services.workflow.dto import ReactFlowDTO, sanitize_workflow_definition
from api.services.workflow.trigger_paths import (
    ensure_trigger_paths,
    extract_trigger_paths,
)
from api.services.workflow.workflow_graph import WorkflowGraph

# Re-export so callers (the route) can map errors without importing the client.
AgentBuilderConfigError = anthropic_client.AgentBuilderConfigError
AgentBuilderClaudeError = anthropic_client.AgentBuilderClaudeError


def _nodes(definition: dict) -> List[dict]:
    nodes = definition.get("nodes")
    return nodes if isinstance(nodes, list) else []


def _node_index(definition: dict) -> Dict[str, dict]:
    return {n.get("id"): n for n in _nodes(definition) if isinstance(n, dict)}


def _attach_tool_uuid(node: dict, tool_uuid: str) -> None:
    data = node.setdefault("data", {})
    if not isinstance(data, dict):
        return
    existing = data.get("tool_uuids")
    if not isinstance(existing, list):
        existing = []
    if tool_uuid not in existing:
        existing.append(tool_uuid)
    data["tool_uuids"] = existing


def _ensure_webhook(definition: dict, tools: List[dict], webhook_url: str) -> None:
    """Ensure a webhook node and an http_api tool point at the provided URL."""
    nodes = _nodes(definition)
    webhook_nodes = [n for n in nodes if isinstance(n, dict) and n.get("type") == "webhook"]
    if webhook_nodes:
        for n in webhook_nodes:
            data = n.setdefault("data", {})
            if isinstance(data, dict):
                data["endpoint_url"] = webhook_url
                data.setdefault("http_method", "POST")
                data.setdefault("enabled", True)
    else:
        nodes.append(
            {
                "id": "order-webhook",
                "type": "webhook",
                "position": {"x": 400, "y": 1000},
                "data": {
                    "name": "Order Webhook",
                    "enabled": True,
                    "http_method": "POST",
                    "endpoint_url": webhook_url,
                    "payload_template": {
                        "call_id": "{{workflow_run_id}}",
                        "outcome": "{{gathered_context.call_disposition}}",
                    },
                },
            }
        )
        definition["nodes"] = nodes

    # Point an existing order-ish http_api tool at the URL, or synthesize one.
    order_tool = next(
        (
            t
            for t in tools
            if isinstance(t, dict)
            and ("order" in (t.get("name") or "").lower() or not (t.get("config") or {}).get("url"))
        ),
        None,
    )
    if order_tool is not None:
        config = order_tool.setdefault("config", {})
        if isinstance(config, dict):
            config["url"] = webhook_url
            config.setdefault("method", "POST")
    else:
        agent_ids = [
            n.get("id") for n in nodes if isinstance(n, dict) and n.get("type") == "agentNode"
        ]
        tools.append(
            {
                "name": "place_order",
                "description": "Submit the customer's order to the order system.",
                "node_ids": agent_ids[-1:],
                "config": {"method": "POST", "url": webhook_url},
            }
        )


async def _create_tools_and_wire(
    definition: dict, tools: List[dict], user: UserModel, warnings: List[str]
) -> None:
    """Create each http_api tool and write its uuid onto the named nodes."""
    index = _node_index(definition)
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        config = tool.get("config") or {}
        url = config.get("url")
        if not name or not url:
            warnings.append(f"Skipped a tool with no name or url: {name!r}")
            continue

        definition_payload = {
            "type": "http_api",
            "config": {
                "method": (config.get("method") or "POST"),
                "url": url,
                "headers": config.get("headers") or None,
                "parameters": config.get("parameters") or None,
                "preset_parameters": config.get("preset_parameters") or None,
            },
        }
        try:
            request = CreateToolRequest(
                name=str(name)[:255],
                description=tool.get("description"),
                category="http_api",
                definition=definition_payload,
            )
            created = await create_tool_for_user(request, user, source="agent_builder")
        except (ToolManagementError, ValidationError, ValueError) as e:
            warnings.append(f"Could not create tool {name!r}: {e}")
            continue

        for node_id in tool.get("node_ids") or []:
            node = index.get(node_id)
            if node is not None:
                _attach_tool_uuid(node, created.tool_uuid)
            else:
                warnings.append(
                    f"Tool {name!r} referenced unknown node id {node_id!r}"
                )


def _validate_definition(definition: dict, warnings: List[str]) -> None:
    """Collect (non-fatal) DTO + graph warnings for the generated definition."""
    dto: Optional[ReactFlowDTO] = None
    try:
        dto = ReactFlowDTO.model_validate(definition)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ()))
            warnings.append(f"{loc}: {err.get('msg')}")
    if dto is not None:
        try:
            WorkflowGraph(dto)
        except ValueError as e:
            errs = e.args[0] if e.args else []
            if isinstance(errs, list):
                for item in errs:
                    warnings.append(str(getattr(item, "message", item)))
            else:
                warnings.append(str(e))


async def generate_agent(request, user: UserModel) -> Dict[str, Any]:
    """Generate a draft workflow from a business prompt. Returns the response dict.

    Raises AgentBuilderConfigError (missing key) and AgentBuilderClaudeError
    (Claude/parse failure); the route maps those to 503/502.
    """
    business: Optional[Dict[str, Any]] = None
    if request.business is not None:
        business = (
            request.business
            if isinstance(request.business, dict)
            else request.business.model_dump(exclude_none=True)
        )

    result = anthropic_client.generate(request.prompt, business)

    definition = result.get("workflow_definition") or {}
    if not isinstance(definition, dict):
        raise AgentBuilderClaudeError("workflow_definition was not a JSON object")
    tools = result.get("tools") or []
    if not isinstance(tools, list):
        tools = []
    model_config = result.get("model_config")
    if not isinstance(model_config, dict):
        model_config = DEFAULT_MODEL_CONFIG
    name = result.get("name") or "AI Generated Agent"

    warnings: List[str] = []

    # If the business supplied an order webhook URL, ensure a webhook node and
    # an http_api tool point at it.
    webhook_url = (business or {}).get("order_webhook_url")
    if webhook_url:
        try:
            _ensure_webhook(definition, tools, str(webhook_url))
        except Exception as e:  # noqa: BLE001 - never fail the build over wiring
            warnings.append(f"Could not wire the order webhook: {e}")

    # Create tools and wire their uuids onto the matching nodes.
    await _create_tools_and_wire(definition, tools, user, warnings)

    # Strip any UI-only keys Claude may have invented, then validate.
    definition = sanitize_workflow_definition(definition) or definition
    definition = ensure_trigger_paths(definition)
    _validate_definition(definition, warnings)

    # Create the workflow, then a draft carrying the model configuration.
    workflow = await db_client.create_workflow(
        str(name),
        definition,
        user.id,
        user.selected_organization_id,
    )
    await db_client.save_workflow_draft(
        workflow.id,
        workflow_definition=definition,
        workflow_configurations={
            WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY: model_config
        },
    )

    # Sync any API trigger nodes (usually none for outbound agents).
    trigger_paths = extract_trigger_paths(definition)
    if trigger_paths:
        try:
            await db_client.assert_trigger_paths_available(trigger_paths=trigger_paths)
            await db_client.sync_triggers_for_workflow(
                workflow_id=workflow.id,
                organization_id=user.selected_organization_id,
                trigger_paths=trigger_paths,
            )
        except TriggerPathConflictError as e:
            warnings.append(f"Trigger path conflict; triggers not synced: {e}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Agent builder trigger sync failed: {e}")
            warnings.append("Triggers could not be synced automatically")

    return {
        "workflow_id": workflow.id,
        "status": "draft",
        "name": workflow.name,
        "warnings": warnings,
        "editor_path": f"/workflow/{workflow.id}",
    }
