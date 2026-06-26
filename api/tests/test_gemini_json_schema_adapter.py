from unittest.mock import patch

from google.genai.types import GenerateContentConfig, LiveConnectConfig
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema

from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.gemini_json_schema_adapter import (
    DograhGeminiJSONSchemaAdapter,
)
from api.services.pipecat.realtime.gemini_live import DograhGeminiLiveLLMService
from api.services.pipecat.realtime.gemini_live_vertex import (
    DograhGeminiLiveVertexLLMService,
)
from api.services.pipecat.service_factory import (
    DograhGoogleLLMService,
    DograhGoogleVertexLLMService,
    create_llm_service_from_provider,
)


def test_gemini_tools_use_json_schema_parameters_for_external_schemas():
    function_schema = FunctionSchema(
        name="customer_lookup",
        description="Look up a customer by email.",
        properties={
            "customerEmail": {
                "description": "Customer email address",
                "anyOf": [
                    {"anyOf": [{"not": {}}]},
                    {"const": ""},
                ],
            },
            "metadata": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
        },
        required=["customerEmail"],
    )

    tools = DograhGeminiJSONSchemaAdapter().to_provider_tools_format(
        ToolsSchema(standard_tools=[function_schema])
    )

    declaration = tools[0]["function_declarations"][0]
    assert "parameters" not in declaration
    assert (
        declaration["parameters_json_schema"]["properties"]["customerEmail"]["anyOf"][
            0
        ]["anyOf"][0]["not"]
        == {}
    )
    assert (
        declaration["parameters_json_schema"]["properties"]["customerEmail"]["anyOf"][
            1
        ]["const"]
        == ""
    )
    assert declaration["parameters_json_schema"]["properties"]["metadata"][
        "additionalProperties"
    ] == {"type": "string"}

    GenerateContentConfig(tools=tools)


def test_gemini_tools_use_json_schema_parameters_for_no_argument_tools():
    function_schema = FunctionSchema(
        name="refresh_context",
        description="Refresh the current context.",
        properties={},
        required=[],
    )

    tools = DograhGeminiJSONSchemaAdapter().to_provider_tools_format(
        ToolsSchema(standard_tools=[function_schema])
    )

    declaration = tools[0]["function_declarations"][0]
    assert "parameters" not in declaration
    assert declaration["parameters_json_schema"] == {
        "type": "object",
        "properties": {},
        "required": [],
    }

    GenerateContentConfig(tools=tools)


def test_google_service_classes_use_dograh_gemini_adapter_class():
    assert DograhGoogleLLMService.adapter_class is DograhGeminiJSONSchemaAdapter
    assert DograhGoogleVertexLLMService.adapter_class is DograhGeminiJSONSchemaAdapter


def test_google_llm_service_factory_uses_dograh_service_class():
    with patch(
        "api.services.pipecat.service_factory.DograhGoogleLLMService",
    ) as mock_service:
        result = create_llm_service_from_provider(
            provider=ServiceProviders.GOOGLE.value,
            model="gemini-2.5-flash",
            api_key="test-api-key",
        )

    assert result is mock_service.return_value
    assert mock_service.call_args.kwargs["api_key"] == "test-api-key"
    assert mock_service.call_args.kwargs["settings"].model == "gemini-2.5-flash"


def test_google_vertex_llm_service_factory_uses_dograh_service_class():
    with patch(
        "api.services.pipecat.service_factory.DograhGoogleVertexLLMService",
    ) as mock_service:
        result = create_llm_service_from_provider(
            provider=ServiceProviders.GOOGLE_VERTEX.value,
            model="gemini-2.5-pro",
            api_key=None,
            project_id="demo-project",
            location="us-central1",
            credentials='{"type":"service_account"}',
        )

    assert result is mock_service.return_value
    assert mock_service.call_args.kwargs["project_id"] == "demo-project"
    assert mock_service.call_args.kwargs["location"] == "us-central1"
    assert mock_service.call_args.kwargs["settings"].model == "gemini-2.5-pro"


def test_gemini_live_service_classes_use_dograh_gemini_adapter_class():
    assert DograhGeminiLiveLLMService.adapter_class is DograhGeminiJSONSchemaAdapter
    # Vertex Live inherits adapter_class from DograhGeminiLiveLLMService via MRO.
    assert (
        DograhGeminiLiveVertexLLMService.adapter_class is DograhGeminiJSONSchemaAdapter
    )


def test_gemini_live_config_accepts_json_schema_tools():
    function_schema = FunctionSchema(
        name="customer_lookup",
        description="Look up a customer by email.",
        properties={
            "customerEmail": {
                "description": "Customer email address",
                "anyOf": [{"not": {}}, {"const": ""}],
            },
        },
        required=["customerEmail"],
    )

    tools = DograhGeminiJSONSchemaAdapter().to_provider_tools_format(
        ToolsSchema(standard_tools=[function_schema])
    )

    declaration = tools[0]["function_declarations"][0]
    assert "parameters" not in declaration
    assert "parameters_json_schema" in declaration

    # Gemini Live validates tools through LiveConnectConfig rather than
    # GenerateContentConfig; it must also accept the raw JSON Schema payload.
    LiveConnectConfig(tools=tools)
