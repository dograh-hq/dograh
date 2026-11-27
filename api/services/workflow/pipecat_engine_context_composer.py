"""System prompt and function schema composition for PipecatEngine nodes.

Extracts prompt and function composition logic from PipecatEngine into
reusable functions. Defines recording response mode markers and instructions.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager
    from api.services.workflow.workflow_graph import Node, WorkflowGraph

from api.schemas.workflow_configurations import PromptVariablesConfiguration
from api.services.workflow.pipecat_engine_custom_tools import get_function_schema
from api.services.workflow.prompt_variables import (
    call_context_note,
    call_context_prompt,
    call_variable_names,
    clock_source_labels,
    prompt_templates,
)
from api.services.workflow.tools.knowledge_base import get_knowledge_base_tool

# ---------------------------------------------------------------------------
# Recording response mode markers
# ---------------------------------------------------------------------------

RECORDING_MARKER = "●"  # Play pre-recorded audio
TTS_MARKER = "▸"  # Generate dynamic TTS text

# ---------------------------------------------------------------------------
# Recording response mode system prompt instructions
# ---------------------------------------------------------------------------

RECORDING_RESPONSE_MODE_INSTRUCTIONS = """\
RESPONSE MODE INSTRUCTIONS - MANDATORY FORMAT:
Every response you generate MUST begin with excatcly one response mode indicator.
You have two modes for responding:

1. DYNAMIC SPEECH (▸): Generate text that will be converted to speech by TTS.
   Format: ▸ followed by a space and your full spoken response. Nothing else.
   Example: ▸ Hello! How can I help you today?

2. PRE-RECORDED AUDIO (●): Play a pre-recorded audio message.
   Format: ● followed by a space followed by recording_id followed by provided transcript. Nothing else.
   Example: ● rec_greeting_01 [ Provided Transcript ]

RULES:
- Your response MUST start with either ▸ or ● as the very first character.
- For ▸ (dynamic speech): Follow with a space and your response to be generated using TTS engine. Dont mix with ●
- For ● (pre-recorded audio): Follow with a space and recording_id of the audio clip with its transcript. Dont mix with ▸
- Use ● when a pre-recorded message matches the situation well.
- Use ▸ when you need to generate a dynamic, contextual response.
- *NEVER* mix modes in a single response, since we rely on the markers to decide whether to play using TTS or Pre-recorded audio."""


@dataclass(frozen=True)
class ComposedNodePrompt:
    text: str
    recording_enabled: bool


def compose_system_prompt_for_node(
    *,
    node: "Node",
    workflow: "WorkflowGraph",
    format_prompt: Callable[[str], str],
    has_recordings: bool,
    prompt_variables: PromptVariablesConfiguration | None = None,
) -> ComposedNodePrompt:
    """Compose the full system prompt text for a workflow node.

    Combines the global prompt, node-specific prompt, and recording response
    mode instructions. Returns the same recording-mode decision for the router.

    Args:
        node: The workflow node to compose the prompt for.
        workflow: The full workflow graph (needed for global node prompt).
        format_prompt: Callable to render template variables in prompts.
        has_recordings: Whether the organization has active recordings.

    Returns:
        The composed text and whether this node uses recording response mode.
    """
    source_labels = {}
    if prompt_variables and prompt_variables.mode == "call_context":
        templates = prompt_templates(workflow)
        names = call_variable_names(templates)

        def format_prompt(text):
            return call_context_prompt(text, names)

        source_labels = clock_source_labels(templates)
    global_prompt = ""
    if workflow.global_node_id and node.add_global_prompt:
        global_node = workflow.nodes[workflow.global_node_id]
        global_prompt = format_prompt(global_node.prompt)
        if workflow.global_node_id in source_labels:
            global_prompt = (
                source_labels[workflow.global_node_id] + "\n" + global_prompt
            )

    formatted_node_prompt = format_prompt(node.prompt)
    if node.id in source_labels:
        formatted_node_prompt = source_labels[node.id] + "\n" + formatted_node_prompt

    parts = [p for p in (global_prompt, formatted_node_prompt) if p]

    recording_enabled = has_recordings and "RECORDING_ID:" in formatted_node_prompt
    if recording_enabled:
        parts.append(RECORDING_RESPONSE_MODE_INSTRUCTIONS)
    if prompt_variables and prompt_variables.mode == "call_context":
        parts.append(call_context_note(prompt_variables))

    return ComposedNodePrompt(
        text="\n\n".join(parts), recording_enabled=recording_enabled
    )


async def compose_functions_for_node(
    *,
    node: "Node",
    custom_tool_manager: Optional["CustomToolManager"] = None,
    custom_tool_schemas: list | None = None,
) -> list[dict]:
    """Compose the function/tool schemas for a workflow node.

    Gathers knowledge-base tools, custom tools (including built-in
    categories like calculator), and transition function schemas
    into a single list.

    Args:
        node: The workflow node to compose functions for.
        custom_tool_manager: Manager for custom and built-in tools (may be None).

    Returns:
        A list of function schemas to register with the LLM.
    """
    functions: list[dict] = []

    # Knowledge base retrieval tool
    if node.document_uuids:
        kb_tool_def = get_knowledge_base_tool(node.document_uuids)
        kb_schema = get_function_schema(
            kb_tool_def["function"]["name"],
            kb_tool_def["function"]["description"],
            properties=kb_tool_def["function"]["parameters"].get("properties", {}),
            required=kb_tool_def["function"]["parameters"].get("required", []),
        )
        functions.append(kb_schema)

    # Custom tools
    if custom_tool_schemas is not None:
        functions.extend(custom_tool_schemas)
    if node.tool_uuids and custom_tool_manager:
        custom_tool_schemas = await custom_tool_manager.get_tool_schemas(
            node.tool_uuids,
            mcp_tool_filters=getattr(node, "mcp_tool_filters", None),
        )
        functions.extend(custom_tool_schemas)

    # Transition function schemas
    for outgoing_edge in node.out_edges:
        function_schema = get_function_schema(
            outgoing_edge.get_function_name(), outgoing_edge.condition
        )
        functions.append(function_schema)

    return functions
