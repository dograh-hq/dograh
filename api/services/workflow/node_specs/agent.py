"""Spec for the Agent node — the workhorse mid-call node where the LLM
executes a focused conversational step with optional tools and documents."""

from api.services.workflow.node_specs._base import (
    DisplayOptions,
    GraphConstraints,
    NodeCategory,
    NodeExample,
    NodeSpec,
    PropertyOption,
    PropertySpec,
    PropertyType,
)

SPEC = NodeSpec(
    name="agentNode",
    display_name="Agent Node",
    description="Conversational step — the LLM runs one focused exchange.",
    llm_hint=(
        "Mid-call step executed by the LLM. Most workflows are a chain of "
        "agent nodes connected by edges that describe transition conditions. "
        "Each agent node can invoke tools and reference documents."
    ),
    category=NodeCategory.call_node,
    icon="Headset",
    properties=[
        PropertySpec(
            name="name",
            type=PropertyType.string,
            display_name="Name",
            description=(
                "Short identifier for this step (e.g., 'Qualify Budget'). "
                "Appears in call logs and edge transition tools."
            ),
            required=True,
            min_length=1,
            default="Agent",
        ),
        PropertySpec(
            name="prompt",
            type=PropertyType.mention_textarea,
            display_name="Prompt",
            description=(
                "Agent system prompt for this step. Supports "
                "{{template_variables}} from extraction or pre-call fetch."
            ),
            required=True,
            min_length=1,
            placeholder="Ask the caller about their budget and timeline.",
        ),
        PropertySpec(
            name="allow_interrupt",
            type=PropertyType.boolean,
            display_name="Allow Interruption",
            description=(
                "When true, the user can interrupt the agent mid-utterance. "
                "Set false for non-interruptible disclosures."
            ),
            default=True,
        ),
        PropertySpec(
            name="add_global_prompt",
            type=PropertyType.boolean,
            display_name="Add Global Prompt",
            description=(
                "When true and a Global node exists, prepends the global "
                "prompt to this node's prompt at runtime."
            ),
            default=True,
        ),
        PropertySpec(
            name="extraction_enabled",
            type=PropertyType.boolean,
            display_name="Enable Variable Extraction",
            description=(
                "When true, runs an LLM extraction pass on transition out of "
                "this node to capture variables from the conversation."
            ),
            default=False,
        ),
        PropertySpec(
            name="extraction_prompt",
            type=PropertyType.string,
            display_name="Extraction Prompt",
            description="Overall instructions guiding variable extraction.",
            display_options=DisplayOptions(show={"extraction_enabled": [True]}),
            editor="textarea",
        ),
        PropertySpec(
            name="extraction_variables",
            type=PropertyType.fixed_collection,
            display_name="Variables to Extract",
            description=(
                "Each entry declares one variable to capture from the "
                "conversation, with its name, type, and per-variable hint."
            ),
            display_options=DisplayOptions(show={"extraction_enabled": [True]}),
            properties=[
                PropertySpec(
                    name="name",
                    type=PropertyType.string,
                    display_name="Variable Name",
                    description="snake_case identifier used downstream.",
                    required=True,
                ),
                PropertySpec(
                    name="type",
                    type=PropertyType.options,
                    display_name="Type",
                    description="Data type of the extracted value.",
                    required=True,
                    default="string",
                    options=[
                        PropertyOption(value="string", label="String"),
                        PropertyOption(value="number", label="Number"),
                        PropertyOption(value="boolean", label="Boolean"),
                    ],
                ),
                PropertySpec(
                    name="prompt",
                    type=PropertyType.string,
                    display_name="Extraction Hint",
                    description="Per-variable hint describing what to look for.",
                    editor="textarea",
                ),
            ],
        ),
        PropertySpec(
            name="tool_uuids",
            type=PropertyType.tool_refs,
            display_name="Tools",
            description="Tools the agent can invoke during this step.",
            llm_hint="List of tool UUIDs from `list_tools`.",
        ),
        PropertySpec(
            name="document_uuids",
            type=PropertyType.document_refs,
            display_name="Knowledge Base Documents",
            description="Documents the agent can reference during this step.",
            llm_hint="List of document UUIDs from `list_documents`.",
        ),
    ],
    examples=[
        NodeExample(
            name="qualify_lead",
            data={
                "name": "Qualify Budget",
                "prompt": "Ask about budget and timeline. Capture both before transitioning.",
                "allow_interrupt": True,
                "extraction_enabled": True,
                "extraction_prompt": "Extract budget amount and rough timeline.",
                "extraction_variables": [
                    {
                        "name": "budget_usd",
                        "type": "number",
                        "prompt": "Stated budget in USD",
                    },
                    {
                        "name": "timeline",
                        "type": "string",
                        "prompt": "When they want to start",
                    },
                ],
            },
        ),
    ],
    graph_constraints=GraphConstraints(min_incoming=1),
)
