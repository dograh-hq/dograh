"""Bounded text rehearsals of SDK workflows with mocked tools only.

Uses production prompt composition, tool schemas and transition names. It does
not execute HTTP, telephony, retrieval, MCP or workflow persistence operations.
Audio timing, extraction, transfers and executable nodes need runtime tests.
"""

import json
import time
from collections import Counter
from typing import Any

from jsonschema import ValidationError as SchemaError
from jsonschema import validate
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field, model_validator

from api.db import db_client
from api.services.workflow.dto import NodeType, ReactFlowDTO
from api.services.workflow.pipecat_engine_context_composer import (
    compose_system_prompt_for_node,
)
from api.services.workflow.tools.calculator import get_calculator_tools
from api.services.workflow.tools.custom_tool import tool_to_function_schema
from api.services.workflow.tools.knowledge_base import get_knowledge_base_tool
from api.services.workflow.workflow_graph import WorkflowGraph
from api.utils.template_renderer import render_template

from .model import BuilderChatModel
from .working_copy import source_revision


class WorkflowScenario(BaseModel):
    caller_messages: list[str] = Field(min_length=1, max_length=8)
    # Function names, not tool UUIDs. Each invocation consumes exactly one result.
    mock_tool_results: dict[str, list[Any]] = Field(default_factory=dict)
    initial_context: dict[str, Any] = Field(default_factory=dict)
    expected_tool_calls: dict[str, int] = Field(default_factory=dict)
    expected_nodes: list[str] = Field(default_factory=list)
    forbidden_phrases: list[str] = Field(default_factory=list)
    required_phrases: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def bounded(self):
        if len(self.model_dump_json()) > 20000:
            raise ValueError("Keep the scenario under 20,000 characters")
        if any(count < 0 for count in self.expected_tool_calls.values()):
            raise ValueError("Expected call counts must be non-negative")
        return self


async def run_workflow_scenario(
    code: str,
    scenario: WorkflowScenario,
    organization_id: int,
    user_id: str,
    *,
    model=None,
) -> dict:
    from api.services.workflow.builder_validation import preview_workflow_code

    preview = await preview_workflow_code(code, organization_id)
    if not preview.get("valid"):
        return {"error": "Validate the workflow before testing.", "validation": preview}
    graph = WorkflowGraph(ReactFlowDTO.model_validate(preview["workflow"]))
    unsupported = [
        node.name
        for node in graph.nodes.values()
        if (
            node.node_type
            not in {
                NodeType.startNode.value,
                NodeType.agentNode.value,
                NodeType.endNode.value,
                NodeType.globalNode.value,
            }
            or node.pre_call_fetch_mode != "disabled"
            or (node.extraction_enabled and node.extraction_variables)
            or node.greeting_type == "audio"
            or "RECORDING_ID:" in (node.prompt or "")
            or any(
                edge.data.transition_speech_type == "audio" for edge in node.out_edges
            )
        )
    ]
    if unsupported:
        return {
            "error": "These nodes require a runtime test; the text simulator cannot cover their execution, extraction or audio behavior.",
            "nodes": unsupported,
        }
    uuids = list(
        {uuid for node in graph.nodes.values() for uuid in node.tool_uuids or []}
    )
    tools = await db_client.get_tools_by_uuids(uuids, organization_id) if uuids else []
    if {tool.tool_uuid for tool in tools} != set(uuids):
        return {
            "error": "One or more tools are no longer available to this organization."
        }
    if any(
        tool.category in {"mcp", "transfer_agent", "transfer_call"} for tool in tools
    ):
        return {
            "error": "MCP and transfer tools require a runtime test. No tools were executed."
        }
    tool_schemas = {
        tool.tool_uuid: get_calculator_tools()
        if tool.category == "calculator"
        else [tool_to_function_schema(tool)]
        for tool in tools
    }
    model = model or BuilderChatModel(
        organization_id=organization_id, user_id=user_id, max_output_tokens=2048
    )
    started = time.monotonic()
    messages, transcript, calls, visited, failures = [], [], [], [], []
    mocks = {
        name: list(results) for name, results in scenario.mock_tool_results.items()
    }
    node = graph.nodes[graph.start_node_id]
    visited.append(node.name)
    model_calls, input_characters, output_characters = 0, 0, 0
    ended = False

    def spoken(text):
        if text:
            transcript.append({"role": "assistant", "node": node.name, "text": text})

    if node.greeting:
        greeting = render_template(node.greeting, scenario.initial_context)
        messages.append(AIMessage(content=greeting))
        spoken(greeting)
    for caller in scenario.caller_messages:
        if ended:
            failures.append("The agent ended before all caller messages were consumed.")
            break
        messages.append(HumanMessage(content=caller))
        transcript.append({"role": "user", "text": caller})
        while model_calls < 16:
            schemas = [
                schema
                for uuid in node.tool_uuids or []
                for schema in tool_schemas[uuid]
            ]
            if node.document_uuids:
                schemas.append(get_knowledge_base_tool(node.document_uuids))
            transitions = (
                {edge.get_function_name(): edge for edge in node.out_edges}
                if not node.is_end
                else {}
            )
            schemas.extend(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": edge.condition,
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                    },
                }
                for name, edge in transitions.items()
            )
            by_name = {
                schema["function"]["name"]: schema["function"] for schema in schemas
            }
            prompt = compose_system_prompt_for_node(
                node=node,
                workflow=graph,
                format_prompt=lambda value: render_template(
                    value or "", scenario.initial_context
                ),
                has_recordings=False,
            ).text
            history = [SystemMessage(content=prompt), *messages]
            if sum(len(str(message.content)) for message in history) > 60000:
                failures.append("Scenario context limit reached.")
                ended = True
                break
            input_characters += sum(
                len(str(message.content)) for message in history
            ) + len(json.dumps(schemas))
            reply = await model.bind_tools(schemas).ainvoke(
                history, authoring_stage="scenario"
            )
            model_calls += 1
            output_characters += len(str(reply.content)) + len(
                json.dumps(reply.tool_calls)
            )
            if reply.additional_kwargs.get("builder_repair"):
                failures.append(
                    "The simulator received incomplete model output; rerun the scenario."
                )
                ended = True
                break
            messages.append(reply)
            spoken(reply.content)
            if not reply.tool_calls:
                ended = node.is_end
                break
            moved = False
            for call in reply.tool_calls:
                name, args = call["name"], call["args"]
                schema = by_name.get(name)
                result = None
                if schema is None or moved:
                    failures.append(f"Unavailable tool call: {name}")
                    result = {"error": "Tool unavailable in this node"}
                else:
                    try:
                        validate(args, schema["parameters"])
                    except SchemaError:
                        failures.append(f"Invalid arguments: {name}")
                        result = {"error": "Arguments do not match the tool schema"}
                if result is None and name in transitions:
                    edge = transitions[name]
                    if edge.transition_speech:
                        spoken(
                            render_template(
                                edge.transition_speech, scenario.initial_context
                            )
                        )
                    node = graph.nodes[edge.target]
                    visited.append(node.name)
                    moved = True
                    result = {"status": "success"}
                elif result is None:
                    calls.append({"name": name, "arguments": args, "node": node.name})
                    if not mocks.get(name):
                        failures.append(f"No mock result remains for {name}")
                        result = {
                            "error": "Missing mock result; no live tool was executed"
                        }
                    else:
                        result = mocks[name].pop(0)
                messages.append(
                    ToolMessage(content=json.dumps(result), tool_call_id=call["id"])
                )
            if failures:
                ended = True
                break
        else:
            failures.append("Scenario model-call limit reached.")
            break
        if failures:
            break
    counts = Counter(call["name"] for call in calls)
    checks = [
        {"assertion": f"{name} called {count} times", "passed": counts[name] == count}
        for name, count in scenario.expected_tool_calls.items()
    ]
    checks += [
        {"assertion": f"Visited {name}", "passed": name in visited}
        for name in scenario.expected_nodes
    ]
    speech = "\n".join(
        item["text"] for item in transcript if item["role"] == "assistant"
    ).casefold()
    checks += [
        {
            "assertion": f"Never said: {phrase}",
            "passed": phrase.casefold() not in speech,
        }
        for phrase in scenario.forbidden_phrases
    ]
    checks += [
        {"assertion": f"Said: {phrase}", "passed": phrase.casefold() in speech}
        for phrase in scenario.required_phrases
    ]
    return {
        "mode": "mocked_text",
        "revision": source_revision(code),
        "passed": not failures and all(check["passed"] for check in checks)
        if checks
        else None,
        "checks": checks,
        "failures": failures,
        "transcript": transcript,
        "tool_calls": calls,
        "visited_nodes": visited,
        "metrics": {
            "model_calls": model_calls,
            "input_characters": input_characters,
            "output_characters": output_characters,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        },
        "coverage": "Text responses and tool selection with supplied mock results. Voice timing and live integrations are not tested.",
    }
