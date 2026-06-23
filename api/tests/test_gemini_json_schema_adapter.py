from types import SimpleNamespace
from unittest.mock import patch

from google.genai.types import GenerateContentConfig
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema

from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.gemini_json_schema_adapter import (
    DograhGeminiJSONSchemaAdapter,
)
from api.services.pipecat.service_factory import (
    _use_dograh_gemini_adapter,
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


def test_google_services_use_dograh_gemini_adapter():
    service = SimpleNamespace(_adapter=object())

    result = _use_dograh_gemini_adapter(service)

    assert result is service
    assert isinstance(service._adapter, DograhGeminiJSONSchemaAdapter)


def test_google_llm_service_factory_installs_dograh_gemini_adapter():
    service = SimpleNamespace(_adapter=object())

    with patch(
        "api.services.pipecat.service_factory.GoogleLLMService",
        return_value=service,
    ) as mock_service:
        result = create_llm_service_from_provider(
            provider=ServiceProviders.GOOGLE.value,
            model="gemini-2.5-flash",
            api_key="test-api-key",
        )

    assert result is service
    assert isinstance(service._adapter, DograhGeminiJSONSchemaAdapter)
    assert mock_service.call_args.kwargs["api_key"] == "test-api-key"
    assert mock_service.call_args.kwargs["settings"].model == "gemini-2.5-flash"


def test_google_vertex_llm_service_factory_installs_dograh_gemini_adapter():
    service = SimpleNamespace(_adapter=object())

    with patch(
        "api.services.pipecat.service_factory.GoogleVertexLLMService",
        return_value=service,
    ) as mock_service:
        result = create_llm_service_from_provider(
            provider=ServiceProviders.GOOGLE_VERTEX.value,
            model="gemini-2.5-pro",
            api_key=None,
            project_id="demo-project",
            location="us-central1",
            credentials='{"type":"service_account"}',
        )

    assert result is service
    assert isinstance(service._adapter, DograhGeminiJSONSchemaAdapter)
    assert mock_service.call_args.kwargs["project_id"] == "demo-project"
    assert mock_service.call_args.kwargs["location"] == "us-central1"
    assert mock_service.call_args.kwargs["settings"].model == "gemini-2.5-pro"
