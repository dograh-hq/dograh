"""Node type constants and helpers for the translator."""

AGENT_NODE_TYPES = {
    "startCall",
    "agentNode",
    "endCall",
}

TOOL_NODE_TYPES = {
    "webhook",
    "qa",
    "trigger",
}

STATIC_NODE_TYPES = {
    "globalNode",
}


def is_agent_node(node_type: str) -> bool:
    return node_type in AGENT_NODE_TYPES


def is_start_node(node_type: str) -> bool:
    return node_type == "startCall"


def is_end_node(node_type: str) -> bool:
    return node_type == "endCall"
