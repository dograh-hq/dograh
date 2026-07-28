"""Validate the LLM function-name namespace within workflow nodes."""

from collections import defaultdict
from typing import Any

from api.db import db_client
from api.enums import ToolCategory
from api.services.workflow.errors import ItemKind, WorkflowError
from api.services.workflow.tools.custom_tool import custom_tool_function_name
from api.services.workflow.workflow_graph import transition_tool_name

_DYNAMIC_SCHEMA_CATEGORIES = {
    ToolCategory.CALCULATOR.value,
    ToolCategory.MCP.value,
}


async def validate_workflow_tool_name_collisions(
    workflow_definition: dict[str, Any] | None,
    organization_id: int,
) -> list[WorkflowError]:
    """Find custom-tool names that collide within a node's LLM namespace.

    Calculator and MCP tool schemas have their own runtime-provided function
    names, so a saved tool's display name cannot be used to validate them here.
    """
    if not workflow_definition:
        return []

    nodes = workflow_definition.get("nodes")
    edges = workflow_definition.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return []

    tool_uuids_by_node: dict[str, set[str]] = {}
    all_tool_uuids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            continue
        data = node.get("data")
        raw_tool_uuids = data.get("tool_uuids") if isinstance(data, dict) else None
        if not isinstance(raw_tool_uuids, list):
            continue
        tool_uuids = {uuid for uuid in raw_tool_uuids if isinstance(uuid, str)}
        if tool_uuids:
            tool_uuids_by_node[node["id"]] = tool_uuids
            all_tool_uuids.update(tool_uuids)

    if not all_tool_uuids:
        return []

    tools = await db_client.get_tools_by_uuids(
        sorted(all_tool_uuids),
        organization_id,
    )
    tools_by_uuid = {
        tool.tool_uuid: tool
        for tool in tools
        if tool.category not in _DYNAMIC_SCHEMA_CATEGORIES
    }

    custom_tools_by_node: dict[str, dict[str, list[Any]]] = {}
    errors: list[WorkflowError] = []
    for node_id, tool_uuids in tool_uuids_by_node.items():
        by_function_name: dict[str, list[Any]] = defaultdict(list)
        for tool_uuid in sorted(tool_uuids):
            tool = tools_by_uuid.get(tool_uuid)
            if tool is not None:
                by_function_name[custom_tool_function_name(tool.name)].append(tool)
        custom_tools_by_node[node_id] = by_function_name

        for function_name, matching_tools in by_function_name.items():
            if len(matching_tools) < 2:
                continue
            tool_names = ", ".join(f'"{tool.name}"' for tool in matching_tools)
            errors.append(
                WorkflowError(
                    kind=ItemKind.node,
                    id=node_id,
                    field="data.tool_uuids",
                    message=(
                        f"Custom tools {tool_names} generate duplicate LLM tool "
                        f'name "{function_name}" on this node. Rename one of the tools.'
                    ),
                )
            )

    for edge in edges:
        if not isinstance(edge, dict):
            continue
        edge_id = edge.get("id")
        source = edge.get("source")
        data = edge.get("data")
        label = data.get("label") if isinstance(data, dict) else None
        if not (
            isinstance(edge_id, str)
            and isinstance(source, str)
            and isinstance(label, str)
            and label
        ):
            continue

        function_name = transition_tool_name(label)
        matching_tools = custom_tools_by_node.get(source, {}).get(function_name, [])
        if not matching_tools:
            continue
        tool_names = ", ".join(f'"{tool.name}"' for tool in matching_tools)
        errors.append(
            WorkflowError(
                kind=ItemKind.edge,
                id=edge_id,
                field="data.label",
                message=(
                    f'Transition tool name "{function_name}" conflicts with custom '
                    f"tool {tool_names} attached to this node. Use a unique edge "
                    "label or rename the custom tool."
                ),
            )
        )

    return errors
