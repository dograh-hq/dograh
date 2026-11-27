"""MCP access to the shared voice-prompting reference."""

from fastapi import HTTPException

from api.mcp_server.auth import authenticate_mcp_request
from api.mcp_server.tracing import traced_tool
from api.services.voice_prompting_guide import list_topic_index, render_guide


@traced_tool
async def get_voice_prompting_guide(topic: str | None = None) -> dict[str, str]:
    """Read voice-prompting guidance before writing or revising prompts.

    For a new agent or broad rewrite, omit topic to get the complete guide.
    For a targeted edit, fetch only the relevant topic and preserve unrelated
    behavior. Reuse guidance already in context; no retrieval is needed for
    changes that do not affect prompts or tool-calling behavior.

    Args:
        topic: Optional topic ID: common_guidelines, guardrails, call_flow_design,
            tool_calls, success_criteria, end_call_logic, turn_taking, or
            instruction_collision. Omit for the complete guide.

    Returns:
        content: The full reference text for the requested topic or all topics.

    Product mechanics belong in node schemas and product documentation.
    """
    await authenticate_mcp_request()
    try:
        return {"content": render_guide(topic)}
    except KeyError:
        available = ", ".join(item["id"] for item in list_topic_index())
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unknown voice-prompting topic: {topic!r}. "
                f"Available topics: {available}."
            ),
        ) from None
