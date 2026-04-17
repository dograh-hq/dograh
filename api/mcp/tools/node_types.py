"""MCP discovery tools for node specifications.

LLMs call these tools first to learn the available node-type catalog and
each node's property schema before composing or modifying a workflow.
"""

from fastapi import HTTPException

from api.mcp.auth import authenticate_mcp_request
from api.mcp.server import mcp
from api.services.workflow.node_specs import SPEC_VERSION, all_specs, get_spec


@mcp.tool
async def list_node_types() -> dict:
    """List every available node type with a brief summary.

    Use this first to discover what nodes exist, then call `get_node_type`
    for the full schema of any node you intend to use.

    Returns:
        A dict with `spec_version` (pin against this in any generated workflow
        code) and `node_types` (list of {name, display_name, description,
        category}).
    """
    await authenticate_mcp_request()
    return {
        "spec_version": SPEC_VERSION,
        "node_types": [
            {
                "name": spec.name,
                "display_name": spec.display_name,
                "description": spec.description,
                "category": spec.category.value,
            }
            for spec in all_specs()
        ],
    }


@mcp.tool
async def get_node_type(name: str) -> dict:
    """Fetch the full schema for a node type, including every property's
    type, default, conditional visibility rules, and LLM-readable
    description, plus worked examples.

    Use the property `description` and the `examples` list to understand
    semantics — types alone are not enough.
    """
    await authenticate_mcp_request()
    spec = get_spec(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown node type: {name!r}")
    return spec.model_dump(mode="json")
