"""Tool registry — maps tool names to factory functions."""

from app.tools.kb_search import make_kb_search_tool


def build_tools(agent_proxy) -> list:
    """Build tools for an agent based on its config."""
    tools = []

    kb_refs = getattr(agent_proxy, "_kb_refs", []) or []
    if kb_refs:
        tools.append(make_kb_search_tool(agent_proxy))

    return tools


TOOL_REGISTRY = {
    "search_knowledge": make_kb_search_tool,
}
