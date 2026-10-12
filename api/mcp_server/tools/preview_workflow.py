from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tracing import traced_tool
from api.services.workflow.builder_validation import preview_workflow_code


@traced_tool
async def preview_workflow(code: str) -> dict:
    """Validate @dograh/sdk TypeScript and return a graph preview without saving.

    Returns valid=true, name, code and workflow on success. Otherwise returns
    valid=false and error/errors. Correct the code and retry after validation errors.
    """
    user = await authenticate_mcp_request()
    return await preview_workflow_code(code, user.selected_organization_id)
