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

    Start with build_requirements: produce a CallBrief and get manual approval.
    Plan designs the conversation and prompts using the voice guide. Build
    translates that plan into SDK source using the included core reference.
    Review checks the candidate against the brief and plan before submission.
    Set iteration=true only for explicitly requested integration changes to an
    existing agent; build/review then include targeted resource lookup guidance.
    Only fetch the stage you need. This tool supplies guidance, not agent
    execution or stage enforcement; the MCP host controls its conversation.
    """
    await authenticate_mcp_request()
    return authoring_stage(stage, iteration=iteration)
