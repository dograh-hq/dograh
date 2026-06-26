"""Dograh-specific Gemini adapter customizations."""

from typing import Any

from pipecat.adapters.schemas.tools_schema import AdapterType, ToolsSchema
from pipecat.adapters.services.gemini_adapter import GeminiLLMAdapter


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
