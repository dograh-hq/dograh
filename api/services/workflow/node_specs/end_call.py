"""Spec for the End Call node — terminal node that wraps up a conversation
and optionally extracts variables before hangup."""

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
    name="endCall",
    display_name="End Call",
    description="Closes the conversation and hangs up.",
    llm_hint=(
        "Terminal node that politely closes the conversation. Variable "
        "extraction can run before hangup. A workflow can have multiple "
        "endCall nodes reached via different edge conditions."
    ),
    category=NodeCategory.call_node,
    icon="OctagonX",
    properties=[
        PropertySpec(
            name="name",
            type=PropertyType.string,
            display_name="Name",
            description=(
                "Short identifier shown in call logs. Should describe the "
                "ending context (e.g., 'Successful close', 'Polite decline')."
            ),
            required=True,
            min_length=1,
            default="End Call",
        ),
        PropertySpec(
            name="prompt",
            type=PropertyType.mention_textarea,
            display_name="Prompt",
            description=(
                "Agent system prompt for the closing exchange. Supports "
                "{{template_variables}} from extraction or pre-call fetch."
            ),
            required=True,
            min_length=1,
            placeholder="Thank the caller and confirm next steps before ending the call.",
        ),
        PropertySpec(
            name="add_global_prompt",
            type=PropertyType.boolean,
            display_name="Add Global Prompt",
            description=(
                "When true and a Global node exists, prepends the global "
                "prompt to this node's prompt at runtime."
            ),
            default=False,
        ),
        PropertySpec(
            name="extraction_enabled",
            type=PropertyType.boolean,
            display_name="Enable Variable Extraction",
            description=(
                "When true, runs an LLM extraction pass before hangup to "
                "capture variables from the conversation."
            ),
            default=False,
        ),
        PropertySpec(
            name="extraction_prompt",
            type=PropertyType.string,
            display_name="Extraction Prompt",
            description=(
                "Overall instructions guiding how variables should be "
                "extracted from the conversation."
            ),
            display_options=DisplayOptions(show={"extraction_enabled": [True]}),
            editor="textarea",
        ),
        PropertySpec(
            name="extraction_variables",
            type=PropertyType.fixed_collection,
            display_name="Variables to Extract",
            description=(
                "Each entry declares one variable to capture from the "
                "conversation, with its name, data type, and a per-variable "
                "extraction hint."
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
                    description="The data type of the extracted value.",
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
                    description=(
                        "Per-variable hint describing what to look for in "
                        "the conversation."
                    ),
                    editor="textarea",
                ),
            ],
        ),
    ],
    examples=[
        NodeExample(
            name="successful_close",
            data={
                "name": "Successful Close",
                "prompt": "Confirm the appointment time, thank the caller, and end the call.",
                "add_global_prompt": False,
            },
        ),
    ],
    graph_constraints=GraphConstraints(
        min_incoming=1,
        min_outgoing=0,
        max_outgoing=0,
    ),
)
