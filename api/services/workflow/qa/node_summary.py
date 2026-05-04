"""Node summary generation and caching for per-node QA analysis."""

from typing import Any

from loguru import logger
from pipecat.processors.aggregators.llm_context import LLMContext

from api.db import db_client
from api.db.models import WorkflowRunModel
from api.services.pipecat.service_factory import create_llm_service_from_provider
from api.services.workflow.dto import NodeType, QANodeData
from api.services.workflow.qa.llm_config import resolve_llm_config
from api.services.workflow.qa.tracing import create_node_summary_trace

NODE_SUMMARY_SYSTEM_PROMPT = (
    "You are analyzing a voice AI agent script. This is only a part of a larger script. "
    "Produce a concise summary (2-4 sentences) describing this script purpose, "
    "what the agent should accomplish, and key behaviors. We will be using this "
    "summary to do a QA on the conversation that the agent would do with someone "
    "so try to capture the nuances of the script as much as possible."
)

CONVERSATION_SUMMARY_SYSTEM_PROMPT = (
    "You are summarizing a portion of a voice AI conversation. "
    "Produce a concise summary (3-5 sentences) covering key topics, "
    "information exchanged, and current state. We would be using this "
    "summary in doing a QA of the conversation that the voice AI agent "
    "did with someone so try to capture the nuances of the conversation "
    "as much as possible."
)


def get_node_summary_text(node_summaries: dict, node_id: str) -> str:
    """Extract the summary text from a node_summaries entry.

    Handles both the current format (dict with "summary" key) and the
    legacy format (plain string) for backward compatibility.
    """
    entry = node_summaries.get(node_id)
    if entry is None:
        return ""
    if isinstance(entry, str):
        return entry
    return entry.get("summary", "")


async def ensure_node_summaries(
    workflow_definition: dict,
    definition_id: int | None,
    workflow_run: WorkflowRunModel,
    qa_data: QANodeData,
) -> dict[str, Any]:
    """Ensure every agentNode/startCall node has a summary in the definition.

    Returns the node_summaries dict:
        {node_id: {"summary": "...", "trace_url": "..."}, ...}
    """
    existing_summaries: dict[str, Any] = workflow_definition.get("node_summaries", {})

    nodes = workflow_definition.get("nodes", [])
    summarizable_types = {NodeType.agentNode.value, NodeType.startNode.value}
    nodes_needing_summary = [
        n
        for n in nodes
        if n.get("type") in summarizable_types and n.get("id") not in existing_summaries
    ]

    if not nodes_needing_summary:
        return existing_summaries

    provider, model, api_key, service_kwargs = await resolve_llm_config(
        qa_data, workflow_run
    )
    if not api_key:
        logger.warning("No API key for node summary generation, skipping")
        return existing_summaries

    llm = create_llm_service_from_provider(provider, model, api_key, **service_kwargs)

    updated_summaries = dict(existing_summaries)

    # Collect all tool UUIDs across nodes and fetch them in one query
    all_tool_uuids: set[str] = set()
    for node in nodes_needing_summary:
        node_data = node.get("data", {})
        for uuid in node_data.get("tool_uuids", []):
            all_tool_uuids.add(uuid)

    tool_map: dict[str, Any] = {}
    if all_tool_uuids:
        organization_id = (
            workflow_run.workflow.organization_id if workflow_run.workflow else None
        )
        if organization_id:
            try:
                tools = await db_client.get_tools_by_uuids(
                    list(all_tool_uuids), organization_id
                )
                for t in tools:
                    tool_map[t.tool_uuid] = {
                        "name": t.name,
                        "description": t.description or "",
                    }
            except Exception as e:
                logger.warning(f"Failed to fetch tools for node summaries: {e}")

    # Build a map of outgoing edges per node (edges are also tool calls)
    edges = workflow_definition.get("edges", [])
    outgoing_edges_by_node: dict[str, list[dict]] = {}
    for edge in edges:
        source = edge.get("source")
        if source:
            outgoing_edges_by_node.setdefault(source, []).append(edge)

    for node in nodes_needing_summary:
        node_id = node["id"]
        node_data = node.get("data", {})
        node_name = node_data.get("name", "Unnamed")

        # Build a description of the node for the LLM
        node_info_parts = [f"Node name: {node_name}"]
        if node_data.get("prompt"):
            node_info_parts.append(f"Agent prompt:\n{node_data['prompt']}")

        # Collect all available tools: custom tools + outgoing edges
        tool_descriptions = []

        node_tool_uuids = node_data.get("tool_uuids", [])
        for uuid in node_tool_uuids:
            tool_info = tool_map.get(uuid)
            if tool_info:
                desc = f"- {tool_info['name']}"
                if tool_info["description"]:
                    desc += f": {tool_info['description']}"
                tool_descriptions.append(desc)

        for edge in outgoing_edges_by_node.get(node_id, []):
            edge_data = edge.get("data", {})
            label = edge_data.get("label", "")
            condition = edge_data.get("condition", "")
            if label:
                desc = f"- {label}"
                if condition:
                    desc += f": {condition}"
                tool_descriptions.append(desc)

        if tool_descriptions:
            node_info_parts.append("Available tools:\n" + "\n".join(tool_descriptions))
        node_info = "\n".join(node_info_parts)
        messages = [
            {"role": "user", "content": node_info},
        ]

        try:
            context = LLMContext()
            context.set_messages(messages)
            summary_text = (
                await llm.run_inference(
                    context, system_instruction=NODE_SUMMARY_SYSTEM_PROMPT
                )
                or ""
            )
        except Exception as e:
            logger.warning(f"Failed to generate summary for node {node_id}: {e}")
            updated_summaries[node_id] = {"summary": ""}
            continue

        # Create a Langfuse trace for this summary generation
        trace_url = create_node_summary_trace(
            model, messages, summary_text, node_name, NODE_SUMMARY_SYSTEM_PROMPT
        )

        entry: dict[str, Any] = {"summary": summary_text}
        if trace_url:
            entry["trace_url"] = trace_url
        updated_summaries[node_id] = entry

    # Persist to DB
    if definition_id and updated_summaries != existing_summaries:
        try:
            await db_client.update_definition_node_summaries(
                definition_id, updated_summaries
            )
        except Exception as e:
            logger.warning(f"Failed to persist node summaries: {e}")

    return updated_summaries
