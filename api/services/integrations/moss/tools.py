from __future__ import annotations

from typing import Any

from loguru import logger

from api.services.integrations.base import ContextProvider, IntegrationTool

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

_AMBIENT_HEADER = (
    "Knowledge base results for the caller's last message. Use them only if "
    "they answer it, and don't mention that you searched."
)


class MossSearch:
    """Searches one Moss index with the settings of a Moss node."""

    def __init__(self, index: MossIndex, top_k: int, alpha: float) -> None:
        self._index = index
        self._top_k = top_k
        self._alpha = alpha
        self._options: Any = None

    def _query_options(self) -> Any:
        if self._options is None:
            from moss import QueryOptions

            self._options = QueryOptions(top_k=self._top_k, alpha=self._alpha)
        return self._options

    async def search(self, query: str) -> dict[str, Any]:
        try:
            client = await self._index.client()
            result = await client.query(self._index.name, query, self._query_options())
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

    async def ambient(self, user_text: str) -> str | None:
        """Results for the caller's turn, or None while the index is still loading."""
        client = self._index.loaded_client()
        if client is None:
            return None
        try:
            result = await client.query(
                self._index.name, user_text, self._query_options()
            )
        except Exception as exc:
            logger.warning(
                f"Moss ambient search on index '{self._index.name}' failed: {exc}"
            )
            return None
        logger.info(
            f"Moss ambient search on index '{self._index.name}': "
            f"{len(result.docs)} results in {result.time_taken_ms} ms"
        )
        if not result.docs:
            return None
        lines = [f"{n}. {doc.text}" for n, doc in enumerate(result.docs, start=1)]
        return "\n".join([_AMBIENT_HEADER, *lines])


def _enabled_node(workflow_graph: Any) -> MossNodeData | None:
    for node in workflow_graph.nodes.values():
        if node.node_type == "moss" and node.data.moss_enabled:
            return node.data
    return None


def _search_for(data: MossNodeData | None) -> MossSearch | None:
    if data is None:
        return None
    project_id = data.moss_project_id
    project_key = data.moss_project_key
    index_name = data.moss_index_name
    if not (project_id and project_key and index_name):
        return None
    index = get_index(project_id, project_key, index_name)
    index.start_loading()
    return MossSearch(index, data.moss_top_k, data.moss_alpha)


def create_tools(
    workflow_graph: Any, *, realtime: bool = False
) -> list[IntegrationTool]:
    data = _enabled_node(workflow_graph)
    # Speech to speech models have no turn to search before, so they get the tool.
    if data is None or (data.moss_mode != "tool" and not realtime):
        return []
    search = _search_for(data)
    if search is None:
        return []

    description = _DESCRIPTION
    contents = (data.moss_index_description or "").strip()
    if contents:
        description = f"{_DESCRIPTION} It contains: {contents}"

    return [
        IntegrationTool(
            name=TOOL_NAME,
            description=description,
            properties=_PROPERTIES,
            required=("query",),
            handler=search.handle,
        )
    ]


def create_context_providers(workflow_graph: Any) -> list[ContextProvider]:
    data = _enabled_node(workflow_graph)
    if data is None or data.moss_mode != "ambient":
        return []
    search = _search_for(data)
    return [search.ambient] if search else []
