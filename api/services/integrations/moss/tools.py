from __future__ import annotations

from typing import Any

from loguru import logger

from api.services.integrations.base import IntegrationTool

from .client import MossIndex, get_index
from .node import MossNodeData

TOOL_NAME = "search_moss_index"

_DESCRIPTION = (
    "Search the knowledge base for facts, policies, procedures, or other "
    "reference information you need to answer the caller."
)

_PROPERTIES = {
    "query": {
        "type": "string",
        "description": (
            "The search query in natural language. Be specific. "
            "Example: 'What is the refund policy for canceled orders?'"
        ),
    }
}


class MossSearch:
    """Searches one Moss index with the settings of a Moss node."""

    def __init__(self, index: MossIndex, top_k: int, alpha: float) -> None:
        self._index = index
        self._top_k = top_k
        self._alpha = alpha
        self._options: Any = None

    async def search(self, query: str) -> dict[str, Any]:
        try:
            client = await self._index.client()
            if self._options is None:
                from moss import QueryOptions

                self._options = QueryOptions(top_k=self._top_k, alpha=self._alpha)
            result = await client.query(self._index.name, query, self._options)
        except Exception as exc:
            logger.error(f"Moss search on index '{self._index.name}' failed: {exc}")
            return {"error": str(exc), "chunks": [], "query": query, "total_results": 0}

        chunks = [
            {
                "text": doc.text,
                "id": doc.id,
                "score": round(doc.score, 4),
                "metadata": doc.metadata or {},
            }
            for doc in result.docs
        ]
        logger.info(
            f"Moss search on index '{self._index.name}': "
            f"{len(chunks)} results in {result.time_taken_ms} ms"
        )
        return {"chunks": chunks, "query": query, "total_results": len(chunks)}

    async def handle(self, params: Any) -> None:
        query = (params.arguments or {}).get("query", "")
        await params.result_callback(await self.search(query))


def create_tools(workflow_graph: Any) -> list[IntegrationTool]:
    for node in workflow_graph.nodes.values():
        if node.node_type != "moss" or not node.data.moss_enabled:
            continue
        data: MossNodeData = node.data
        project_id = data.moss_project_id
        project_key = data.moss_project_key
        index_name = data.moss_index_name
        if not (project_id and project_key and index_name):
            continue
        index = get_index(project_id, project_key, index_name)
        index.start_loading()

        description = _DESCRIPTION
        contents = (data.moss_index_description or "").strip()
        if contents:
            description = f"{_DESCRIPTION} It contains: {contents}"

        search = MossSearch(index, data.moss_top_k, data.moss_alpha)
        return [
            IntegrationTool(
                name=TOOL_NAME,
                description=description,
                properties=_PROPERTIES,
                required=("query",),
                handler=search.handle,
            )
        ]
    return []
