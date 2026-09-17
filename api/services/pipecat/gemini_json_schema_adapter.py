"""Dograh-specific Gemini adapter customizations."""

from typing import Any

from pipecat.adapters.schemas.tools_schema import AdapterType, ToolsSchema
from pipecat.adapters.services.gemini_adapter import GeminiLLMAdapter
from pipecat.adapters.services.gemini_live_adapter import GeminiLiveLLMAdapter


class DograhGeminiJSONSchemaAdapter(GeminiLLMAdapter):
    """Use Gemini's full JSON Schema tool parameter field.

    Pipecat's default Gemini adapter maps ``FunctionSchema.parameters`` into
    ``FunctionDeclaration.parameters``, which is backed by Google GenAI's
    stricter OpenAPI-style ``Schema`` model. MCP and imported tools may contain
    valid JSON Schema keywords such as ``const`` and ``not`` that are rejected
    by that model. ``parameters_json_schema`` is the Google GenAI field intended
    for full JSON Schema payloads.
    """

    def to_provider_tools_format(
        self, tools_schema: ToolsSchema
    ) -> list[dict[str, Any]]:
        functions_schema = tools_schema.standard_tools
        if functions_schema:
            formatted_functions = []
            for func in functions_schema:
                func_dict = func.to_default_dict()
                parameters = func_dict.pop("parameters")
                func_dict["parameters_json_schema"] = parameters
                formatted_functions.append(func_dict)
            formatted_standard_tools = [{"function_declarations": formatted_functions}]
        else:
            formatted_standard_tools = []

        custom_gemini_tools = []
        if tools_schema.custom_tools:
            custom_gemini_tools = tools_schema.custom_tools.get(AdapterType.GEMINI, [])

        return formatted_standard_tools + custom_gemini_tools


class DograhGeminiLiveJSONSchemaAdapter(
    GeminiLiveLLMAdapter, DograhGeminiJSONSchemaAdapter
):
    """Gemini Live adapter with the JSON Schema tool-parameter fix.

    Combines :class:`GeminiLiveLLMAdapter` (tool calls and results converted to
    text, which is all Gemini Live's API accepts when seeding a session) with
    the ``parameters_json_schema`` tool formatting above.
    """

    # Enabled per service instance by ``DograhGeminiLiveLLMService``. When
    # True, the native Google Search tool is appended to every Live session
    # configuration alongside workflow function declarations. ``from_standard_tools``
    # is the single funnel for all session tool payloads (initial connect,
    # node-transition reconnects, error reconnects), so Search survives every
    # reconnect without any per-connect call sites.
    google_search_enabled: bool = False

    # Enabled per service instance for gemini-3.8-live only. Google made async
    # (NON_BLOCKING) the default function-calling mode on 3.8 Live while
    # keeping synchronous BLOCKING for backwards compatibility; Dograh's
    # engine (transition deferral, greeting/terminal flows) assumes blocking
    # turns, so declarations are tagged explicitly. Never enabled for older
    # models (untagged preserves their exact prior payloads).
    force_blocking_tools: bool = False

    def from_standard_tools(self, tools):
        converted = super().from_standard_tools(tools)
        if self.force_blocking_tools and converted:
            converted = [
                (
                    {
                        **tool,
                        "function_declarations": [
                            {**declaration, "behavior": "BLOCKING"}
                            for declaration in tool.get("function_declarations", [])
                        ],
                    }
                    if isinstance(tool, dict) and "function_declarations" in tool
                    else tool
                )
                for tool in converted
            ]
        if not self.google_search_enabled:
            return converted
        tools_list = list(converted) if converted else []
        if not any(
            isinstance(tool, dict) and "google_search" in tool for tool in tools_list
        ):
            tools_list = [*tools_list, {"google_search": {}}]
        return tools_list
