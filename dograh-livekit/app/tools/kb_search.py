"""Knowledge base search tool — calls Dograh API."""

import logging
from livekit.agents import function_tool

logger = logging.getLogger(__name__)


def make_kb_search_tool(agent_proxy):
    """Create a search_knowledge function_tool that calls Dograh API."""

    @function_tool
    async def search_knowledge(query: str) -> str:
        """Search the organization's knowledge base for relevant information.

        query: the search query in natural language
        Returns: formatted search results from the knowledge base.
        """
        from app.dograh_client import DograhClient
        from app.config import settings

        client = DograhClient(settings)
        try:
            results = await client.search_knowledge(
                agent_proxy._org_id,
                query,
                agent_proxy._kb_refs,
            )
        except Exception as exc:
            logger.warning("KB search failed: %s", exc)
            return "Knowledge base search unavailable at this moment."

        items = results.get("results", [])
        if not items:
            return "No relevant information found in the knowledge base."

        formatted = []
        for item in items[:5]:
            content = item.get("content", "")
            source = item.get("source", "unknown")
            if content:
                formatted.append(f"[{source}] {content}")

        return "\n\n".join(formatted)

    return search_knowledge
