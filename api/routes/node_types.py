"""API for the node-spec catalog.

Exposes the registered NodeSpecs (one per node type) so frontend renderers
and the LLM SDK can build forms / typed constructors from a single source
of truth.

Endpoints:
    GET /node-types          → list every registered NodeSpec
    GET /node-types/{name}   → single NodeSpec by name
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.db.models import UserModel
from api.sdk_expose import sdk_expose
from api.services.auth.depends import get_user
from api.services.workflow.node_specs import (
    SPEC_VERSION,
    NodeSpec,
    all_specs,
    get_spec,
)

router = APIRouter(prefix="/node-types")


class NodeTypesResponse(BaseModel):
    spec_version: str
    node_types: list[NodeSpec]


@router.get(
    "",
    response_model=NodeTypesResponse,
    **sdk_expose(
        method="list_node_types",
        description="List every registered node type with its spec. Pinned to spec_version.",
    ),
)
async def list_node_types(
    _user: UserModel = Depends(get_user),
) -> NodeTypesResponse:
    """List every registered NodeSpec.

    SDK clients should pin to `spec_version` and warn if the server reports
    a higher version than what they were generated against.
    """
    return NodeTypesResponse(spec_version=SPEC_VERSION, node_types=all_specs())


@router.get(
    "/{name}",
    response_model=NodeSpec,
    **sdk_expose(
        method="get_node_type",
        description="Fetch a single node spec by name.",
    ),
)
async def get_node_type(
    name: str,
    _user: UserModel = Depends(get_user),
) -> NodeSpec:
    spec = get_spec(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown node type: {name!r}")
    return spec
