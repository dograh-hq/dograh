"""Canvas MCP Engine for In-Canvas AI Copilot.

Provides atomic, tool-callable actions for the Copilot LLM to inspect, mutate,
connect, and test workflow canvas nodes, edges, and fields directly.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger

from api.db import db_client
from api.services.workflow.dto import ReactFlowDTO


CANVAS_MCP_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "regenerate_full_workflow",
            "description": "Regenerate or rebuild the entire visual workflow from scratch based on a comprehensive prompt or new campaign specifications. Use this whenever the user asks to 'regenerate my workflow', 'rebuild completely', or provides a full prompt with requirements. NEVER delete nodes one by one!",
            "parameters": {
                "type": "object",
                "properties": {
                    "use_case": {
                        "type": "string",
                        "description": "Concise use-case title (e.g. 'n8n Course Lead Generation').",
                    },
                    "prompt_instructions": {
                        "type": "string",
                        "description": "The full prompt, rules, requirements, and lead qualification criteria.",
                    },
                    "call_type": {
                        "type": "string",
                        "enum": ["INBOUND", "OUTBOUND"],
                        "description": "Call direction.",
                    },
                },
                "required": ["use_case", "prompt_instructions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_canvas_state",
            "description": "Fetch the current workflow graph structure, including all nodes, their IDs, types, names, prompts, greetings, and all connected edges.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "attach_tool_to_node",
            "description": "Attach a reusable tool from /tools (e.g. Transfer Call, Transfer To Agent, HTTP API, Calculator, MCP) to an agent node on the visual workflow canvas so the voice agent can call it during a phone call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "The exact ID of the node to attach the tool to (e.g. '2' or 'node-3a450d').",
                    },
                    "tool_identifier": {
                        "type": "string",
                        "description": "The name or tool_uuid of the tool from available tools (e.g. 'Transfer to Support', 'Transfer Call', '0ea5e9b1...').",
                    },
                },
                "required": ["node_id", "tool_identifier"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detach_tool_from_node",
            "description": "Remove an attached tool from a canvas node.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "The exact ID of the node (e.g. '2').",
                    },
                    "tool_identifier": {
                        "type": "string",
                        "description": "The name or tool_uuid of the tool to detach.",
                    },
                },
                "required": ["node_id", "tool_identifier"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_node_field",
            "description": "Update a specific field on an existing canvas node. Supported fields: 'prompt', 'greeting', 'name', 'allow_interrupt', 'extraction_enabled', 'extraction_prompt', 'extraction_variables', 'tool_uuids'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "The exact ID of the node to update (e.g. 'agent-1', 'start-1', '2').",
                    },
                    "field_name": {
                        "type": "string",
                        "enum": ["prompt", "greeting", "name", "allow_interrupt", "extraction_enabled", "extraction_prompt", "extraction_variables", "endpoint_url", "qa_system_prompt", "enabled", "tool_uuids"],
                        "description": "The name of the field to update.",
                    },
                    "new_value": {
                        "description": "The new value (string, boolean, array of variables, or array of tool UUIDs).",
                    },
                },
                "required": ["node_id", "field_name", "new_value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_canvas_node",
            "description": "Add a new node to the workflow canvas (agentNode, trigger, webhook, qa, endCall, globalNode). Supports automatic positioning, variable extraction, and routing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_type": {
                        "type": "string",
                        "enum": ["agentNode", "trigger", "webhook", "qa", "endCall", "globalNode"],
                        "description": "Type of node to create: 'agentNode' for conversational steps, 'trigger' for API Trigger, 'webhook' for post-call data sync, 'qa' for post-call QA analysis.",
                    },
                    "name": {
                        "type": "string",
                        "description": "User-facing title of the node (e.g. 'Collect Details & Schedule Demo', 'Sync Lead Webhook').",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Detailed system prompt and instructions for this node.",
                    },
                    "tool_uuids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of tool names or tool_uuids to attach to this node upon creation.",
                    },
                    "extraction_enabled": {
                        "type": "boolean",
                        "description": "Set to true if this node collects lead information (name, email, phone, demo time).",
                    },
                    "extraction_prompt": {
                        "type": "string",
                        "description": "Instructions for LLM extraction pass (e.g. 'Extract user contact details and demo preferences').",
                    },
                    "extraction_variables": {
                        "type": "array",
                        "description": "List of variables to capture downstream.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "snake_case variable name (e.g. 'lead_name', 'email', 'phone', 'demo_time')"},
                                "type": {"type": "string", "enum": ["string", "number", "boolean"], "description": "Data type"},
                                "prompt": {"type": "string", "description": "Hint describing what to extract"}
                            },
                            "required": ["name", "type"]
                        }
                    },
                    "connect_from_node_id": {
                        "type": "string",
                        "description": "Optional ID of the source node to connect from (e.g. '2'). Automatically creates incoming edge.",
                    },
                    "connect_from_label": {
                        "type": "string",
                        "description": "Label for the incoming edge (e.g. 'Interested – Schedule Demo').",
                    },
                    "connect_from_condition": {
                        "type": "string",
                        "description": "Condition for moving to this node.",
                    },
                    "connect_to_node_id": {
                        "type": "string",
                        "description": "Optional ID of target node to connect to after this node finishes (e.g. '4' endCall). Automatically creates outgoing edge.",
                    },
                    "connect_to_label": {
                        "type": "string",
                        "description": "Label for the outgoing edge (e.g. 'Details Collected – End Call').",
                    },
                    "connect_to_condition": {
                        "type": "string",
                        "description": "Condition for concluding this step.",
                    }
                },
                "required": ["node_type", "name", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_variable_extraction",
            "description": "Configure or update variable extraction settings on an existing canvas node to capture structured data (caller name, email, phone number, demo date).",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "The exact ID of the node to configure extraction on (e.g. '2', 'node-3a450d').",
                    },
                    "extraction_enabled": {
                        "type": "boolean",
                        "description": "Whether extraction is enabled (true/false).",
                    },
                    "extraction_prompt": {
                        "type": "string",
                        "description": "Guidance on how to extract details.",
                    },
                    "extraction_variables": {
                        "type": "array",
                        "description": "List of extraction variables.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Variable key name (e.g. 'email')"},
                                "type": {"type": "string", "enum": ["string", "number", "boolean"]},
                                "prompt": {"type": "string", "description": "Extraction hint"}
                            },
                            "required": ["name", "type"]
                        }
                    }
                },
                "required": ["node_id", "extraction_enabled", "extraction_variables"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "connect_nodes",
            "description": "Create or update a directed edge from a source node to a target node with a condition and descriptive label. If a connection between these two nodes already exists, it updates its label and condition instead of creating an overlapping duplicate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_node_id": {
                        "type": "string",
                        "description": "ID of the originating node.",
                    },
                    "target_node_id": {
                        "type": "string",
                        "description": "ID of the destination node.",
                    },
                    "label": {
                        "type": "string",
                        "description": "Short label badge to display on the edge (e.g. 'Interested – Schedule Demo', 'End Call').",
                    },
                    "condition": {
                        "type": "string",
                        "description": "Clear natural language condition explaining when this transition happens.",
                    },
                },
                "required": ["source_node_id", "target_node_id", "label", "condition"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_edge",
            "description": "Update an existing edge's label or condition between two nodes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_node_id": {
                        "type": "string",
                        "description": "ID of the source node.",
                    },
                    "target_node_id": {
                        "type": "string",
                        "description": "ID of the target node.",
                    },
                    "edge_id": {
                        "type": "string",
                        "description": "Optional specific edge ID.",
                    },
                    "label": {
                        "type": "string",
                        "description": "New label for the edge.",
                    },
                    "condition": {
                        "type": "string",
                        "description": "New condition for the edge.",
                    },
                },
                "required": ["source_node_id", "target_node_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_canvas_node",
            "description": "Remove a node from the canvas by ID, automatically removing any connected edges.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "ID of the node to delete.",
                    },
                },
                "required": ["node_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_edge",
            "description": "Remove a specific connection between nodes by edge_id or by source_node_id and target_node_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "edge_id": {
                        "type": "string",
                        "description": "Optional ID of the edge to delete.",
                    },
                    "source_node_id": {
                        "type": "string",
                        "description": "Source node ID of the edge to delete.",
                    },
                    "target_node_id": {
                        "type": "string",
                        "description": "Target node ID of the edge to delete.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_test_call",
            "description": "Launch an immediate simulated test call (WebRTC) so the user can hear and test the agent.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


def execute_canvas_mcp_tool(
    tool_name: str,
    tool_args: Dict[str, Any],
    workflow_definition: Dict[str, Any],
    available_tools: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Dict[str, Any], str, List[str], bool]:
    """
    Execute an atomic canvas MCP tool against workflow_definition.
    Returns:
        (updated_workflow_definition, result_summary, modified_node_ids, should_trigger_test_call)
    """
    nodes = list(workflow_definition.get("nodes", []))
    edges = list(workflow_definition.get("edges", []))
    modified_node_ids: List[str] = []
    should_test = False
    result_summary = ""

    if tool_name == "get_canvas_state":
        tool_names_by_uuid = {t.get("tool_uuid"): t.get("name") for t in (available_tools or []) if t.get("tool_uuid")}
        node_summaries = []
        for n in nodes:
            n_tools = n.get("data", {}).get("tool_uuids") or []
            tool_strs = [f"'{tool_names_by_uuid.get(u, u)}'" for u in n_tools]
            tools_info = f", Attached Tools: [{', '.join(tool_strs)}]" if tool_strs else ""
            node_summaries.append(
                f"- Node {n.get('id')} ({n.get('type')}): '{n.get('data', {}).get('name')}'{tools_info}"
            )
        edge_summaries = [
            f"- Edge {e.get('source')} ──[{e.get('data', {}).get('label')}]──► {e.get('target')}"
            for e in edges
        ]
        result_summary = (
            f"Current graph has {len(nodes)} nodes and {len(edges)} edges:\n"
            + "\n".join(node_summaries)
            + "\nConnections:\n"
            + "\n".join(edge_summaries)
        )

    elif tool_name == "attach_tool_to_node":
        node_id = str(tool_args.get("node_id", "")).strip()
        tool_ident = str(tool_args.get("tool_identifier", "")).strip()
        target_node = next((n for n in nodes if str(n.get("id")) == node_id), None)
        if not target_node:
            result_summary = f"Node with ID '{node_id}' not found."
        else:
            matched_uuid = tool_ident
            matched_name = tool_ident
            if available_tools:
                ident_lower = tool_ident.lower()
                for t in available_tools:
                    if t.get("tool_uuid") == tool_ident or t.get("name", "").strip().lower() == ident_lower:
                        matched_uuid = t.get("tool_uuid")
                        matched_name = t.get("name")
                        break
            data = target_node.setdefault("data", {})
            current_tools = list(data.get("tool_uuids") or [])
            if matched_uuid not in current_tools:
                current_tools.append(matched_uuid)
                data["tool_uuids"] = current_tools
                modified_node_ids.append(node_id)
                result_summary = f"Attached tool '{matched_name}' to node '{data.get('name', node_id)}'."
            else:
                result_summary = f"Tool '{matched_name}' is already attached to node '{data.get('name', node_id)}'."

    elif tool_name == "detach_tool_from_node":
        node_id = str(tool_args.get("node_id", "")).strip()
        tool_ident = str(tool_args.get("tool_identifier", "")).strip()
        target_node = next((n for n in nodes if str(n.get("id")) == node_id), None)
        if not target_node:
            result_summary = f"Node with ID '{node_id}' not found."
        else:
            matched_uuid = tool_ident
            matched_name = tool_ident
            if available_tools:
                ident_lower = tool_ident.lower()
                for t in available_tools:
                    if t.get("tool_uuid") == tool_ident or t.get("name", "").strip().lower() == ident_lower:
                        matched_uuid = t.get("tool_uuid")
                        matched_name = t.get("name")
                        break
            data = target_node.setdefault("data", {})
            current_tools = list(data.get("tool_uuids") or [])
            if matched_uuid in current_tools:
                current_tools.remove(matched_uuid)
                data["tool_uuids"] = current_tools
                modified_node_ids.append(node_id)
                result_summary = f"Detached tool '{matched_name}' from node '{data.get('name', node_id)}'."
            else:
                result_summary = f"Tool '{matched_name}' was not attached to node '{data.get('name', node_id)}'."

    elif tool_name == "update_node_field":
        node_id = tool_args.get("node_id")
        field = tool_args.get("field_name")
        val = tool_args.get("new_value")

        target_node = next((n for n in nodes if n.get("id") == node_id), None)
        if not target_node:
            result_summary = f"Node with ID '{node_id}' not found."
        else:
            data = target_node.setdefault("data", {})
            if field in ("allow_interrupt", "extraction_enabled", "qa_enabled", "enabled"):
                data[field] = str(val).lower() in ("true", "1", "yes") if not isinstance(val, bool) else val
            elif field == "extraction_variables":
                data[field] = val if isinstance(val, list) else []
            elif field == "tool_uuids":
                data[field] = val if isinstance(val, list) else [val]
            else:
                data[field] = str(val)
            modified_node_ids.append(node_id)
            result_summary = f"Updated '{field}' on node '{data.get('name', node_id)}'."

    elif tool_name == "configure_variable_extraction":
        node_id = tool_args.get("node_id")
        enabled = bool(tool_args.get("extraction_enabled", True))
        prompt_val = tool_args.get("extraction_prompt", "")
        variables = tool_args.get("extraction_variables", [])

        target_node = next((n for n in nodes if n.get("id") == node_id), None)
        if not target_node:
            result_summary = f"Node with ID '{node_id}' not found."
        else:
            data = target_node.setdefault("data", {})
            data["extraction_enabled"] = enabled
            data["extraction_prompt"] = prompt_val
            data["extraction_variables"] = variables if isinstance(variables, list) else []
            modified_node_ids.append(node_id)
            var_names = [v.get("name") for v in data["extraction_variables"] if isinstance(v, dict)]
            result_summary = f"Configured variable extraction on node '{data.get('name', node_id)}' for variables: {', '.join(var_names)}."

    elif tool_name == "add_canvas_node":
        n_type = tool_args.get("node_type", "agentNode")
        name = tool_args.get("name", "New Node")
        prompt = tool_args.get("prompt", "Assist caller.")
        new_id = f"node-{uuid.uuid4().hex[:6]}"

        connect_from = tool_args.get("connect_from_node_id")
        connect_to = tool_args.get("connect_to_node_id")

        # Position cleanly next to source or stacked
        src_node = next((n for n in nodes if n.get("id") == connect_from), None) if connect_from else None
        if src_node and isinstance(src_node.get("position"), dict):
            src_pos = src_node["position"]
            pos_x = src_pos.get("x", 250)
            pos_y = src_pos.get("y", 200) + 240
        else:
            pos_x = 250
            pos_y = 200 + (len(nodes) - 1) * 120

        if n_type == "trigger":
            raw_path = tool_args.get("trigger_path") or name or "lead_trigger"
            clean_path = re.sub(r"[^a-z0-9_-]", "_", str(raw_path).lower()).strip("_")
            if not clean_path or clean_path in ("api_trigger", "new_node", "trigger"):
                clean_path = f"trigger_{uuid.uuid4().hex[:6]}"
            data_payload = {
                "name": name if name not in ("New Node", "agentNode") else "API Trigger",
                "enabled": True,
                "trigger_path": clean_path,
            }
        elif n_type == "webhook":
            data_payload = {
                "name": name if name not in ("New Node", "agentNode") else "Post-Call Webhook",
                "enabled": True,
                "http_method": tool_args.get("http_method", "POST"),
                "endpoint_url": tool_args.get("endpoint_url", "https://api.example.com/webhook"),
                "payload_template": {
                    "call_id": "{{workflow_run_id}}",
                    "caller_name": "{{gathered_context.lead_name}}",
                    "email": "{{gathered_context.email}}",
                    "phone": "{{gathered_context.phone}}",
                },
            }
            pos_x = 650
            pos_y = 1350
        elif n_type == "qa":
            data_payload = {
                "name": name if name not in ("New Node", "agentNode") else "QA Analysis",
                "qa_enabled": True,
                "qa_system_prompt": prompt if prompt != "Assist caller." else "Analyze the call transcript for lead qualification, customer intent, and budget.",
                "qa_min_call_duration": 15,
                "qa_sample_rate": 100,
            }
            pos_x = 1100
            pos_y = 1350
        else:
            raw_tools = tool_args.get("tool_uuids", [])
            resolved_tools = []
            if isinstance(raw_tools, list) and raw_tools:
                for ti in raw_tools:
                    m_uuid = str(ti)
                    if available_tools:
                        ti_lower = str(ti).strip().lower()
                        for t in available_tools:
                            if t.get("tool_uuid") == ti or t.get("name", "").strip().lower() == ti_lower:
                                m_uuid = t.get("tool_uuid")
                                break
                    resolved_tools.append(m_uuid)

            data_payload = {
                "name": name,
                "prompt": prompt,
                "allow_interrupt": True,
                "tool_uuids": resolved_tools,
                "add_global_prompt": True,
            }

            # Variable extraction settings
            ext_enabled = bool(tool_args.get("extraction_enabled", False))
            if ext_enabled:
                data_payload["extraction_enabled"] = True
                data_payload["extraction_prompt"] = tool_args.get(
                    "extraction_prompt", "Extract user contact details and demo preferences."
                )
                data_payload["extraction_variables"] = tool_args.get("extraction_variables", [])
            else:
                data_payload["extraction_enabled"] = False
                data_payload["extraction_variables"] = []

        new_node = {
            "id": new_id,
            "type": n_type,
            "position": {"x": pos_x, "y": pos_y},
            "data": data_payload,
        }
        nodes.append(new_node)
        modified_node_ids.append(new_id)

        added_actions = [f"Created new {n_type} '{data_payload.get('name', name)}' (ID: {new_id})"]

        # Auto-connect incoming edge (only for conversational nodes)
        if connect_from and n_type not in ("trigger", "webhook", "qa"):
            in_label = tool_args.get("connect_from_label", "Interested – Schedule Demo")
            in_cond = tool_args.get("connect_from_condition", "Proceed when caller is interested in scheduling a demo.")
            edges.append({
                "id": f"e-{connect_from}-{new_id}-{uuid.uuid4().hex[:4]}",
                "source": connect_from,
                "target": new_id,
                "type": "custom",
                "animated": True,
                "data": {"label": in_label, "condition": in_cond},
            })
            added_actions.append(f"connected from '{connect_from}' with label '{in_label}'")

        # Auto-connect outgoing edge
        if connect_to:
            out_label = tool_args.get("connect_to_label", "Details Collected – End Call")
            out_cond = tool_args.get("connect_to_condition", "Details have been collected, end the call gracefully.")
            edges.append({
                "id": f"e-{new_id}-{connect_to}-{uuid.uuid4().hex[:4]}",
                "source": new_id,
                "target": connect_to,
                "type": "custom",
                "animated": True,
                "data": {"label": out_label, "condition": out_cond},
            })
            added_actions.append(f"connected to '{connect_to}' with label '{out_label}'")

        result_summary = " and ".join(added_actions) + "."

    elif tool_name == "connect_nodes":
        source = str(tool_args.get("source_node_id", "")).strip()
        target = str(tool_args.get("target_node_id", "")).strip()
        label = tool_args.get("label", "Next Step")
        condition = tool_args.get("condition", "Proceed when ready.")

        # Check if an edge already exists between source and target
        existing_edge = next((e for e in edges if str(e.get("source")) == source and str(e.get("target")) == target), None)
        if existing_edge:
            data = existing_edge.setdefault("data", {})
            data["label"] = label
            data["condition"] = condition
            existing_edge["type"] = "custom"
            existing_edge["animated"] = True
            result_summary = f"Updated existing connection from node '{source}' to '{target}' with label '{label}' and condition: '{condition}'."
        else:
            edge_id = f"e-{source}-{target}-{uuid.uuid4().hex[:4]}"
            new_edge = {
                "id": edge_id,
                "source": source,
                "target": target,
                "type": "custom",
                "animated": True,
                "data": {
                    "label": label,
                    "condition": condition,
                },
            }
            edges.append(new_edge)
            result_summary = f"Connected node '{source}' to '{target}' with label '{label}'."

    elif tool_name == "update_edge":
        source = str(tool_args.get("source_node_id", "")).strip()
        target = str(tool_args.get("target_node_id", "")).strip()
        edge_id = tool_args.get("edge_id")
        label = tool_args.get("label")
        condition = tool_args.get("condition")

        target_edge = None
        if edge_id:
            target_edge = next((e for e in edges if e.get("id") == edge_id), None)
        if not target_edge and source and target:
            target_edge = next((e for e in edges if str(e.get("source")) == source and str(e.get("target")) == target), None)

        if target_edge:
            data = target_edge.setdefault("data", {})
            if label:
                data["label"] = label
            if condition:
                data["condition"] = condition
            target_edge["type"] = "custom"
            target_edge["animated"] = True
            result_summary = f"Updated edge between node '{source}' and '{target}'."
        else:
            result_summary = f"Could not find edge to update between node '{source}' and '{target}'."

    elif tool_name == "remove_canvas_node":
        node_id = tool_args.get("node_id")
        target_node = next((n for n in nodes if n.get("id") == node_id), None)
        if target_node and target_node.get("type") in ("startCall", "endCall"):
            result_summary = f"Cannot delete '{target_node.get('type')}' node because it is required for voice calling. You can update its prompt or greeting instead."
        else:
            nodes = [n for n in nodes if n.get("id") != node_id]
            edges = [e for e in edges if e.get("source") != node_id and e.get("target") != node_id]
            result_summary = f"Removed node '{node_id}' and all attached edges."

    elif tool_name == "remove_edge":
        edge_id = tool_args.get("edge_id")
        source = tool_args.get("source_node_id")
        target = tool_args.get("target_node_id")
        if edge_id:
            edges = [e for e in edges if e.get("id") != edge_id]
            result_summary = f"Deleted edge '{edge_id}'."
        elif source and target:
            orig_len = len(edges)
            edges = [e for e in edges if not (str(e.get("source")) == str(source) and str(e.get("target")) == str(target))]
            deleted_count = orig_len - len(edges)
            result_summary = f"Deleted {deleted_count} edge(s) between node '{source}' and '{target}'."
        else:
            result_summary = "Could not delete edge: please provide edge_id or source_node_id and target_node_id."

    elif tool_name == "trigger_test_call":
        should_test = True
        result_summary = "Triggered WebRTC test call session."

    else:
        result_summary = f"Unknown tool: {tool_name}"

    # Ensure every node has a valid position dict for ReactFlowDTO
    for idx, n in enumerate(nodes):
        if not isinstance(n.get("position"), dict):
            n["position"] = {"x": 250, "y": 60 + idx * 180}

    # Re-pack and validate graph
    updated_def = {
        "nodes": nodes,
        "edges": edges,
        "viewport": workflow_definition.get("viewport", {"x": 0, "y": 0, "zoom": 1}),
    }

    # Ensure ReactFlow compatibility
    try:
        ReactFlowDTO.model_validate({"nodes": nodes, "edges": edges})
    except Exception as exc:
        logger.warning("MCP action resulted in strict validation warning: {}", exc)

    return updated_def, result_summary, modified_node_ids, should_test
