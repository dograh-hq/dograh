"""Progressive authoring guidance shared with the in-app agents."""

from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tracing import traced_tool
from api.services.workflow.authoring import authoring_stage
from api.services.workflow.authoring.contracts import AuthoringStage


@traced_tool
async def get_workflow_authoring_guide(
    stage: AuthoringStage, iteration: bool = False
) -> dict:
    """Retrieve just the current authoring stage and its structured output schema.

    For a new agent, start with build_requirements: produce a CallBrief and get manual approval.
    Build turns that brief directly into conversation flow, prompts, and SDK
    source using the voice guide and included core reference. Review checks
    the candidate against the brief before submission.
    For any edit to an existing agent, use build with iteration=true directly.
    This selects the editor prompt, without requiring a new brief, and includes
    targeted resource lookup guidance. Review edits with iteration=true as well.
    Only fetch the stage you need. This tool supplies guidance, not agent
    execution or stage enforcement; the MCP host controls its conversation.
    """
    await authenticate_mcp_request()
    return authoring_stage(stage, iteration=iteration)
