"""Translator: Dograh ReactFlow JSON → Agno Workflow."""

from __future__ import annotations

import logging
from typing import Any

from agno.agent import Agent
from agno.workflow import Step, Workflow
from agno.workflow.router import Router

from app.models import WorkflowGraph
from app.translator.nodes import is_agent_node, is_end_node

logger = logging.getLogger(__name__)


def translate_workflow(graph: WorkflowGraph, agent_config: dict[str, Any]) -> Workflow:
    """Convert Dograh workflow graph into an Agno Workflow."""
    nodes = graph.nodes
    edges = graph.edges

    if not nodes:
        return Workflow(name=f"dograh_{graph.id or 'empty'}", steps=[])

    agent_nodes = [n for n in nodes if is_agent_node(n.type)]

    edge_index: dict[str, dict[str, str]] = {}
    for e in edges:
        cond = e.data.condition or "*"
        edge_index.setdefault(e.source, {})[cond] = e.target

    global_prompt = ""
    for n in nodes:
        if n.type == "globalNode" and n.data.prompt:
            if global_prompt:
                global_prompt += "\n\n"
            global_prompt += n.data.prompt

    agents_by_id: dict[str, Agent] = {}
    for node in agent_nodes:
        instructions = node.data.prompt or ""
        if node.data.add_global_prompt and global_prompt:
            instructions = global_prompt + "\n\n" + instructions
        if agent_config.get("system_prompt"):
            instructions = agent_config["system_prompt"] + "\n\n" + instructions

        agents_by_id[node.id] = Agent(
            name=node.data.name or node.id,
            instructions=instructions,
        )

    steps: list[Step | Router] = []
    for node in agent_nodes:
        node_id = node.id
        agent = agents_by_id[node_id]

        step_kwargs: dict = {}
        if is_end_node(node.type):
            step_kwargs["name"] = f"{node_id}_end"
        else:
            step_kwargs["name"] = node_id
        steps.append(Step(agent=agent, **step_kwargs))

        outgoing = edge_index.get(node_id, {})
        non_star_keys = [k for k in outgoing if k != "*"]
        if len(non_star_keys) >= 2:
            choices: list[Step] = []
            choice_ids: set[str] = set()
            for route_key, target_id in outgoing.items():
                if target_id not in choice_ids and target_id in agents_by_id:
                    choice_ids.add(target_id)
                    choices.append(Step(name=target_id, agent=agents_by_id[target_id]))

            if choices:
                fallback_id = outgoing.get("*")
                fallback_step = None
                if fallback_id and fallback_id in agents_by_id:
                    fallback_step = Step(name=fallback_id, agent=agents_by_id[fallback_id])

                def make_selector(
                    rt: dict[str, str],
                    fallback: Step | None,
                    all_choices: list[Step],
                ):
                    def selector(step_input, step_choices):
                        name_to_step = {s.name: s for s in step_choices}
                        previous = (
                            step_input.previous_step_outputs
                            if hasattr(step_input, "previous_step_outputs")
                            else {}
                        )
                        for _prev_name, prev_output in (
                            previous.items() if isinstance(previous, dict) else []
                        ):
                            content = (
                                getattr(prev_output, "content", "")
                                if hasattr(prev_output, "content")
                                else str(prev_output)
                            )
                            content_lower = str(content).lower()
                            for route_key, target_id in rt.items():
                                if route_key != "*" and route_key in content_lower:
                                    if target_id in name_to_step:
                                        return name_to_step[target_id]
                        if fallback and fallback.name in name_to_step:
                            return name_to_step[fallback.name]
                        return step_choices[-1] if step_choices else None

                    return selector

                router = Router(
                    name=f"{node_id}_router",
                    selector=make_selector(outgoing, fallback_step, choices),
                    choices=choices,
                )
                steps.append(router)

    return Workflow(
        name=f"dograh_{graph.id or 'wf'}",
        steps=steps,
    )
