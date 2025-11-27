from __future__ import annotations

from typing import Any, Dict, List

from api.utils.template_renderer import render_template
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.processors.aggregators.llm_context import LLMContext

__all__ = [
    "get_function_schema",
    "update_llm_context",
    "render_template",
]


def get_function_schema(
    function_name: str,
    description: str,
    *,
    properties: Dict[str, Any] | None = None,
    required: List[str] | None = None,
) -> FunctionSchema:
    """Create a FunctionSchema definition that can later be transformed into
    the provider-specific format (OpenAI, Gemini, etc.).

    The helper keeps the public signature backward-compatible – callers that
    only pass ``function_name`` and ``description`` continue to work and will
    define a parameter-less function.
    """

    return FunctionSchema(
        name=function_name,
        description=description,
        properties=properties or {},
        required=required or [],
    )


def update_llm_context(
    context: LLMContext,
    system_message: Dict[str, Any],
    functions: List[FunctionSchema],
) -> None:
    """Update *context* with an up-to-date system message and tool list.

    This helper removes any previous system messages before inserting the new
    *system_message* at the top of the conversation history and then instructs
    the LLM which *functions* (a.k.a. tools) are currently available.
    """

    # Wrap the provided function schemas in a ToolsSchema so that the adapter
    # associated with the current LLM service can convert them to the correct
    # provider-specific representation when required.
    tools_schema = ToolsSchema(standard_tools=functions)
    previous_interactions = context.messages

    # Filter out old system messages but keep user/assistant/function content.
    messages: List[Dict[str, Any]] = [system_message]
    messages.extend(
        interaction
        for interaction in previous_interactions
        if interaction["role"] != "system"
    )

    context.set_messages(messages)

    if functions:
        context.set_tools(tools_schema)
