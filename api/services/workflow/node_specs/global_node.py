"""Spec for the Global node — system-level instructions appended to every
agent node that opts in via `add_global_prompt`."""

from api.services.workflow.node_specs._base import (
    GraphConstraints,
    NodeCategory,
    NodeExample,
    NodeSpec,
    PropertySpec,
    PropertyType,
)

SPEC = NodeSpec(
    name="globalNode",
    display_name="Global Node",
    description=(
        "System-level prompt appended to every agent node whose "
        "`add_global_prompt` is true. Use it for persona, tone, and "
        "shared rules that apply across the entire conversation."
    ),
    category=NodeCategory.global_node,
    icon="Globe",
    properties=[
        PropertySpec(
            name="name",
            type=PropertyType.string,
            display_name="Name",
            description=(
                "Short identifier shown in the canvas and call logs. Has no "
                "runtime effect."
            ),
            required=True,
            min_length=1,
        ),
        PropertySpec(
            name="prompt",
            type=PropertyType.mention_textarea,
            display_name="Global Prompt",
            description=(
                "Text appended to every prompted node's system prompt when "
                "that node has `add_global_prompt=true`. Supports "
                "{{template_variables}}."
            ),
            required=True,
            min_length=1,
            placeholder="You are a friendly assistant calling on behalf of {{company_name}}.",
        ),
    ],
    examples=[
        NodeExample(
            name="basic_persona",
            description="Establishes a consistent persona across the call.",
            data={
                "name": "Persona",
                "prompt": (
                    "You are Sarah, a polite and warm representative from "
                    "Acme Corp. Always thank the caller for their time and "
                    "speak in short conversational sentences."
                ),
            },
        ),
    ],
    graph_constraints=GraphConstraints(
        min_incoming=0,
        max_incoming=0,
        min_outgoing=0,
        max_outgoing=0,
    ),
)
