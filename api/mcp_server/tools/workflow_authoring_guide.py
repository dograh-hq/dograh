"""Progressive authoring guidance shared with the in-app agents."""

from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tracing import traced_tool
from api.services.workflow.authoring import authoring_stage
from api.services.workflow.authoring.contracts import AuthoringStage


@traced_tool
async def get_workflow_authoring_guide(stage: AuthoringStage) -> dict:
    """Retrieve just the current authoring stage and its structured output schema.

    Start with plan: discover the customer's use case and produce a CallBrief.
    Build translates the settled brief into workflow source and includes SDK
    syntax. Review checks the candidate against the brief before submission.
    Only fetch the stage you need. This tool supplies guidance, not agent
    execution or stage enforcement; the MCP host controls its conversation.
    """
    await authenticate_mcp_request()
    return authoring_stage(stage)
