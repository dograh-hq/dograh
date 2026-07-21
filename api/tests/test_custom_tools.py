"""Tests for custom tool integration with PipecatEngine.

This module tests:
1. tool_to_function_schema - converting tool models to LLM function schemas
2. execute_http_tool - executing HTTP API tools
3. CustomToolManager - tool registration and handler execution
4. End-to-end LLM generation with custom tool calls
"""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    FunctionCallsFromLLMInfoFrame,
    FunctionCallsStartedFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMServiceMetadataFrame,
    UserTurnInferenceCompletedFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.llm_service import FunctionCallParams

from api.enums import WorkflowRunMode
from api.services.configuration.masking import mask_key
from api.services.workflow.pipecat_engine_custom_tools import get_function_schema
from api.services.workflow.tools.custom_tool import (
    _coerce_parameter_value,
    execute_http_tool,
    tool_to_function_schema,
)
from pipecat.tests import MockLLMService, run_test


@dataclass
class MockToolModel:
    """Mock tool model for testing."""

    tool_uuid: str
    name: str
    description: str
    category: str
    definition: Dict[str, Any]


def test_empty_resolver_credential_uuid_is_ignored():
    from api.services.tool_management import _credential_uuids_from_definition

    definition = {
        "schema_version": 1,
        "type": "transfer_call",
        "config": {
            "destination_source": "dynamic",
            "resolver": {
                "type": "http",
                "url": "https://crm.example.com/resolve-transfer",
                "credential_uuid": "",
            },
        },
    }

    assert _credential_uuids_from_definition(definition) == []


class TestToolToFunctionSchema:
    """Tests for tool_to_function_schema function."""

    def test_simple_tool_with_string_parameter(self):
        """Test converting a simple tool with one string parameter."""
        tool = MockToolModel(
            tool_uuid="test-uuid-1",
            name="Get Weather",
            description="Get current weather for a location",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "GET",
                    "url": "https://api.weather.com/current",
                    "parameters": [
                        {
                            "name": "location",
                            "type": "string",
                            "description": "City name",
                            "required": True,
                        }
                    ],
                },
            },
        )

        schema = tool_to_function_schema(tool)

        assert schema["type"] == "function"
        assert schema["function"]["name"] == "get_weather"
        assert schema["function"]["description"] == "Get current weather for a location"
        assert schema["function"]["parameters"]["type"] == "object"
        assert "location" in schema["function"]["parameters"]["properties"]
        assert (
            schema["function"]["parameters"]["properties"]["location"]["type"]
            == "string"
        )
        assert (
            schema["function"]["parameters"]["properties"]["location"]["description"]
            == "City name"
        )
        assert "location" in schema["function"]["parameters"]["required"]
        assert schema["_tool_uuid"] == "test-uuid-1"

    def test_tool_with_multiple_parameter_types(self):
        """Test converting a tool with string, number, and boolean parameters."""
        tool = MockToolModel(
            tool_uuid="test-uuid-2",
            name="Book Appointment",
            description="Book an appointment with the service",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "POST",
                    "url": "https://api.example.com/appointments",
                    "parameters": [
                        {
                            "name": "customer_name",
                            "type": "string",
                            "description": "Customer's full name",
                            "required": True,
                        },
                        {
                            "name": "duration_minutes",
                            "type": "number",
                            "description": "Appointment duration in minutes",
                            "required": True,
                        },
                        {
                            "name": "is_priority",
                            "type": "boolean",
                            "description": "Whether this is a priority appointment",
                            "required": False,
                        },
                    ],
                },
            },
        )

        schema = tool_to_function_schema(tool)

        props = schema["function"]["parameters"]["properties"]
        assert props["customer_name"]["type"] == "string"
        assert props["duration_minutes"]["type"] == "number"
        assert props["is_priority"]["type"] == "boolean"

        required = schema["function"]["parameters"]["required"]
        assert "customer_name" in required
        assert "duration_minutes" in required
        assert "is_priority" not in required

    def test_tool_with_object_and_array_parameters(self):
        """Test converting a tool with object and array parameters."""
        tool = MockToolModel(
            tool_uuid="test-uuid-nested",
            name="Create Booking",
            description="Create a booking with nested details",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "POST",
                    "url": "https://api.example.com/bookings",
                    "parameters": [
                        {
                            "name": "booking",
                            "type": "object",
                            "description": "Nested booking payload",
                            "required": True,
                        },
                        {
                            "name": "attendees",
                            "type": "array",
                            "description": "Booking attendees",
                            "required": False,
                        },
                    ],
                },
            },
        )

        schema = tool_to_function_schema(tool)

        props = schema["function"]["parameters"]["properties"]
        assert props["booking"] == {
            "type": "object",
            "additionalProperties": True,
            "description": "Nested booking payload",
        }
        assert props["attendees"] == {
            "type": "array",
            "items": {},
            "description": "Booking attendees",
        }

    def test_preset_parameters_are_not_exposed_to_llm_schema(self):
        """Test that preset parameters are injected at runtime, not shown to the LLM."""
        tool = MockToolModel(
            tool_uuid="test-uuid-preset",
            name="Lookup Customer",
            description="Lookup a customer using contextual identifiers",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "POST",
                    "url": "https://api.example.com/customers/lookup",
                    "parameters": [
                        {
                            "name": "customer_name",
                            "type": "string",
                            "description": "Customer name spoken by the caller",
                            "required": True,
                        }
                    ],
                    "preset_parameters": [
                        {
                            "name": "phone_number",
                            "type": "string",
                            "value_template": "{{initial_context.phone_number}}",
                            "required": True,
                        }
                    ],
                },
            },
        )

        schema = tool_to_function_schema(tool)
        props = schema["function"]["parameters"]["properties"]

        assert "customer_name" in props
        assert "phone_number" not in props

    def test_tool_name_sanitization(self):
        """Test that tool names with special characters are sanitized."""
        tool = MockToolModel(
            tool_uuid="test-uuid-3",
            name="Get User's Account Info!!!",
            description="Get account information",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "GET",
                    "url": "https://api.example.com/account",
                    "parameters": [],
                },
            },
        )

        schema = tool_to_function_schema(tool)

        # Name should be lowercase with underscores only
        assert schema["function"]["name"] == "get_user_s_account_info"

    def test_tool_with_no_parameters(self):
        """Test converting a tool with no parameters."""
        tool = MockToolModel(
            tool_uuid="test-uuid-4",
            name="Ping Server",
            description="Check if server is alive",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "GET",
                    "url": "https://api.example.com/ping",
                },
            },
        )

        schema = tool_to_function_schema(tool)

        assert schema["function"]["parameters"]["properties"] == {}
        assert schema["function"]["parameters"]["required"] == []

    def test_tool_without_description_uses_fallback(self):
        """Test that tools without description use fallback."""
        tool = MockToolModel(
            tool_uuid="test-uuid-5",
            name="My Tool",
            description=None,
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "POST",
                    "url": "https://api.example.com/tool",
                },
            },
        )

        schema = tool_to_function_schema(tool)

        assert schema["function"]["description"] == "Execute My Tool tool"

    def test_transfer_tool_schema_includes_configured_parameters(self):
        """Transfer tools can expose resolver inputs to the model."""
        tool = MockToolModel(
            tool_uuid="transfer-uuid",
            name="Transfer To Partner",
            description="Transfer to the correct referral partner",
            category="transfer_call",
            definition={
                "schema_version": 1,
                "type": "transfer_call",
                "config": {
                    "destination_source": "dynamic",
                    "destination": "",
                    "resolver": {
                        "type": "http",
                        "url": "https://crm.example.com/resolve-transfer",
                        "parameters": [
                            {
                                "name": "state",
                                "type": "string",
                                "description": "The caller's US state.",
                                "required": True,
                            },
                            {
                                "name": "reason",
                                "type": "string",
                                "description": "Why transfer is needed.",
                                "required": False,
                            },
                        ],
                    },
                },
            },
        )

        schema = tool_to_function_schema(tool)

        params = schema["function"]["parameters"]
        assert params["properties"]["state"]["type"] == "string"
        assert params["properties"]["reason"]["type"] == "string"
        assert params["required"] == ["state"]

    def test_transfer_tool_schema_handles_null_resolver_parameters(self):
        """Dynamic transfer tools should remain available with no parameters."""
        tool = MockToolModel(
            tool_uuid="transfer-uuid",
            name="Transfer To Partner",
            description="Transfer to the correct referral partner",
            category="transfer_call",
            definition={
                "schema_version": 1,
                "type": "transfer_call",
                "config": {
                    "destination_source": "dynamic",
                    "resolver": {
                        "type": "http",
                        "url": "https://crm.example.com/resolve-transfer",
                        "parameters": None,
                    },
                },
            },
        )

        schema = tool_to_function_schema(tool)

        assert schema["function"]["parameters"]["properties"] == {}
        assert schema["function"]["parameters"]["required"] == []


class TestExecuteHttpTool:
    """Tests for execute_http_tool function."""

    @pytest.mark.asyncio
    async def test_post_request_sends_json_body(self):
        """Test that POST requests send arguments as JSON body."""
        tool = MockToolModel(
            tool_uuid="test-uuid",
            name="Create User",
            description="Create a new user",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "POST",
                    "url": "https://api.example.com/users",
                    "timeout_ms": 5000,
                },
            },
        )

        arguments = {"name": "John", "email": "john@example.com"}

        with patch(
            "api.services.workflow.tools.custom_tool.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.status_code = 201
            mock_response.json.return_value = {"id": 123, "name": "John"}
            mock_client.request.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await execute_http_tool(tool, arguments)

            # Verify request was made with JSON body
            mock_client.request.assert_called_once()
            call_kwargs = mock_client.request.call_args.kwargs
            assert call_kwargs["method"] == "POST"
            assert call_kwargs["url"] == "https://api.example.com/users"
            assert call_kwargs["json"] == arguments
            assert call_kwargs["params"] is None

            assert result["status"] == "success"
            assert result["status_code"] == 201
            assert result["data"]["id"] == 123
            assert "request_headers" not in result

    @pytest.mark.asyncio
    async def test_post_request_sends_nested_json_body(self):
        """Test that POST requests preserve nested arguments in the JSON body."""
        tool = MockToolModel(
            tool_uuid="test-uuid-nested",
            name="Create Booking",
            description="Create a nested booking",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "POST",
                    "url": "https://api.example.com/bookings",
                    "timeout_ms": 5000,
                },
            },
        )

        arguments = {
            "booking": {
                "start": "2026-05-28T10:00:00Z",
                "attendee": {"name": "Jane", "email": "jane@example.com"},
                "metadata": {"source": "voice"},
            }
        }

        with patch(
            "api.services.workflow.tools.custom_tool.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"bookingId": "booking-123"}
            mock_client.request.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await execute_http_tool(tool, arguments)

            call_kwargs = mock_client.request.call_args.kwargs
            assert call_kwargs["json"] == arguments
            assert isinstance(call_kwargs["json"]["booking"], dict)
            assert isinstance(call_kwargs["json"]["booking"]["attendee"], dict)
            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_post_request_injects_preset_parameters(self):
        """Test that preset parameters are resolved from runtime context."""
        tool = MockToolModel(
            tool_uuid="test-uuid-preset",
            name="Create Lead",
            description="Create a lead with caller context",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "POST",
                    "url": "https://api.example.com/leads",
                    "timeout_ms": 5000,
                    "preset_parameters": [
                        {
                            "name": "phone_number",
                            "type": "string",
                            "value_template": "{{initial_context.phone_number}}",
                            "required": True,
                        },
                        {
                            "name": "customer_id",
                            "type": "number",
                            "value_template": "{{gathered_context.customer_id}}",
                            "required": True,
                        },
                        {
                            "name": "is_vip",
                            "type": "boolean",
                            "value_template": "{{initial_context.is_vip}}",
                            "required": False,
                        },
                    ],
                },
            },
        )

        arguments = {"name": "John"}

        with patch(
            "api.services.workflow.tools.custom_tool.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.status_code = 201
            mock_response.json.return_value = {"id": 123}
            mock_client.request.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await execute_http_tool(
                tool,
                arguments,
                call_context_vars={
                    "phone_number": "+14155550123",
                    "is_vip": "true",
                },
                gathered_context_vars={"customer_id": "42"},
            )

            call_kwargs = mock_client.request.call_args.kwargs
            assert call_kwargs["json"] == {
                "name": "John",
                "phone_number": "+14155550123",
                "customer_id": 42,
                "is_vip": True,
            }
            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_post_request_accepts_pre_resolved_preset_params(self):
        """The test endpoint can supply preset values without call context."""
        tool = MockToolModel(
            tool_uuid="test-uuid-preset-override",
            name="Create Lead",
            description="Create a lead with caller context",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "POST",
                    "url": "https://api.example.com/leads",
                    "timeout_ms": 5000,
                    "preset_parameters": [
                        {
                            "name": "phone_number",
                            "type": "string",
                            "value_template": "{{initial_context.phone_number}}",
                            "required": True,
                        }
                    ],
                },
            },
        )

        with patch(
            "api.services.workflow.tools.custom_tool.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.status_code = 201
            mock_response.json.return_value = {"id": 123}
            mock_client.request.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await execute_http_tool(
                tool,
                {"name": "John"},
                preset_params={"phone_number": "+14155550123"},
            )

            assert mock_client.request.call_args.kwargs["json"] == {
                "name": "John",
                "phone_number": "+14155550123",
            }
            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_missing_required_preset_parameter_returns_error(self):
        """Test that required preset parameters fail before the HTTP request."""
        tool = MockToolModel(
            tool_uuid="test-uuid-preset-error",
            name="Create Lead",
            description="Create a lead with caller context",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "POST",
                    "url": "https://api.example.com/leads",
                    "timeout_ms": 5000,
                    "preset_parameters": [
                        {
                            "name": "phone_number",
                            "type": "string",
                            "value_template": "{{initial_context.phone_number}}",
                            "required": True,
                        }
                    ],
                },
            },
        )

        result = await execute_http_tool(tool, {"name": "John"}, call_context_vars={})

        assert result["status"] == "error"
        assert "phone_number" in result["error"]

    @pytest.mark.asyncio
    async def test_get_request_sends_query_params(self):
        """Test that GET requests send arguments as query parameters."""
        tool = MockToolModel(
            tool_uuid="test-uuid",
            name="Search Users",
            description="Search for users",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "GET",
                    "url": "https://api.example.com/users/search",
                    "timeout_ms": 5000,
                },
            },
        )

        arguments = {"query": "john", "limit": 10}

        with patch(
            "api.services.workflow.tools.custom_tool.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"users": []}
            mock_client.request.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await execute_http_tool(tool, arguments)

            # Verify request was made with query params
            call_kwargs = mock_client.request.call_args.kwargs
            assert call_kwargs["method"] == "GET"
            assert call_kwargs["json"] is None
            assert call_kwargs["params"] == arguments

            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_get_request_serializes_object_and_array_query_params(self):
        """Object/array-typed arguments must be JSON-stringified for GET query
        params — httpx raises a TypeError if a dict/list is passed as-is."""
        tool = MockToolModel(
            tool_uuid="test-uuid",
            name="Search Users",
            description="Search for users",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "GET",
                    "url": "https://api.example.com/users/search",
                    "timeout_ms": 5000,
                },
            },
        )

        arguments = {
            "source": "voice",
            "metadata": {"campaign": "spring"},
            "tags": ["a", "b"],
        }

        with patch(
            "api.services.workflow.tools.custom_tool.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"users": []}
            mock_client.request.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await execute_http_tool(tool, arguments)

            call_kwargs = mock_client.request.call_args.kwargs
            assert call_kwargs["params"] == {
                "source": "voice",
                "metadata": '{"campaign": "spring"}',
                "tags": '["a", "b"]',
            }
            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_get_request_without_arguments_preserves_url_query_params(self):
        """Empty runtime args should not override query params already in the URL."""
        tool = MockToolModel(
            tool_uuid="test-uuid",
            name="Search Users",
            description="Search for users",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "GET",
                    "url": "https://api.example.com/users/search?tenant=abc",
                    "timeout_ms": 5000,
                },
            },
        )

        with patch(
            "api.services.workflow.tools.custom_tool.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"users": []}
            mock_client.request.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await execute_http_tool(tool, {})

            call_kwargs = mock_client.request.call_args.kwargs
            assert call_kwargs["method"] == "GET"
            assert call_kwargs["url"].endswith("?tenant=abc")
            assert call_kwargs["params"] is None
            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_delete_request_sends_query_params(self):
        """Test that DELETE requests send arguments as query parameters."""
        tool = MockToolModel(
            tool_uuid="test-uuid",
            name="Delete User",
            description="Delete a user",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "DELETE",
                    "url": "https://api.example.com/users",
                    "timeout_ms": 5000,
                },
            },
        )

        arguments = {"user_id": "123"}

        with patch(
            "api.services.workflow.tools.custom_tool.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.status_code = 204
            mock_response.json.return_value = {}
            mock_client.request.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            await execute_http_tool(tool, arguments)

            call_kwargs = mock_client.request.call_args.kwargs
            assert call_kwargs["method"] == "DELETE"
            assert call_kwargs["json"] is None
            assert call_kwargs["params"] == arguments

    @pytest.mark.asyncio
    async def test_timeout_error_handling(self):
        """Test that timeout errors are handled gracefully."""
        import httpx

        tool = MockToolModel(
            tool_uuid="test-uuid",
            name="Slow API",
            description="A slow API call",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "POST",
                    "url": "https://api.example.com/slow",
                    "timeout_ms": 1000,
                },
            },
        )

        with patch(
            "api.services.workflow.tools.custom_tool.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client.request.side_effect = httpx.TimeoutException(
                "Request timed out"
            )
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await execute_http_tool(tool, {})

            assert result["status"] == "error"
            assert "timed out" in result["error"]

    @pytest.mark.asyncio
    async def test_request_includes_custom_headers(self):
        """Test that custom headers are included in the request."""
        tool = MockToolModel(
            tool_uuid="test-uuid",
            name="API with Headers",
            description="API that requires headers",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "POST",
                    "url": "https://api.example.com/data",
                    "headers": {
                        "X-API-Key": "secret-key",
                        "X-Custom-Header": "custom-value",
                    },
                    "timeout_ms": 5000,
                },
            },
        )

        with patch(
            "api.services.workflow.tools.custom_tool.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"success": True}
            mock_client.request.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await execute_http_tool(
                tool, {"data": "test"}, include_request_headers=True
            )

            call_kwargs = mock_client.request.call_args.kwargs
            assert call_kwargs["headers"]["X-API-Key"] == "secret-key"
            assert call_kwargs["headers"]["X-Custom-Header"] == "custom-value"
            assert result["request_headers"] == {
                "X-API-Key": "secret-key",
                "X-Custom-Header": "custom-value",
            }

    @pytest.mark.asyncio
    async def test_request_includes_auth_header_from_credential(self):
        """Test that auth headers from credentials are included in the request."""
        tool = MockToolModel(
            tool_uuid="test-uuid",
            name="Authenticated API",
            description="API that requires authentication",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "POST",
                    "url": "https://api.example.com/secure",
                    "credential_uuid": "cred-uuid-123",
                    "timeout_ms": 5000,
                },
            },
        )

        # Mock credential
        mock_credential = Mock()
        mock_credential.name = "API Token"
        mock_credential.credential_type = "bearer_token"
        mock_credential.credential_data = {"token": "my-secret-token"}

        with patch(
            "api.services.workflow.tools.custom_tool.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"success": True}
            mock_client.request.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            with patch("api.services.workflow.tools.custom_tool.db_client") as mock_db:
                mock_db.get_credential_by_uuid = AsyncMock(return_value=mock_credential)

                result = await execute_http_tool(
                    tool,
                    {"data": "test"},
                    organization_id=1,
                    include_request_headers=True,
                )

                # Verify credential was fetched
                mock_db.get_credential_by_uuid.assert_called_once_with(
                    "cred-uuid-123", 1
                )

                # Verify auth header was added
                call_kwargs = mock_client.request.call_args.kwargs
                assert (
                    call_kwargs["headers"]["Authorization"] == "Bearer my-secret-token"
                )
                assert result["request_headers"]["Authorization"] == mask_key(
                    "Bearer my-secret-token"
                )
                assert (
                    "my-secret-token" not in result["request_headers"]["Authorization"]
                )

    @pytest.mark.asyncio
    async def test_no_credential_lookup_without_organization_id(self):
        """Test that credential lookup is skipped without organization_id."""
        tool = MockToolModel(
            tool_uuid="test-uuid",
            name="API with Credential",
            description="API with credential configured",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "POST",
                    "url": "https://api.example.com/secure",
                    "credential_uuid": "cred-uuid-123",
                    "timeout_ms": 5000,
                },
            },
        )

        with patch(
            "api.services.workflow.tools.custom_tool.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"success": True}
            mock_client.request.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            with patch("api.services.workflow.tools.custom_tool.db_client") as mock_db:
                # Call without organization_id
                await execute_http_tool(tool, {"data": "test"})

                # Verify credential lookup was NOT called
                mock_db.get_credential_by_uuid.assert_not_called()


class TestCoerceParameterValue:
    """Tests for _coerce_parameter_value function."""

    def test_object_value_returns_dict_unchanged(self):
        """Test that object parameters preserve dict values."""
        value = {"attendee": {"name": "Jane"}}

        assert _coerce_parameter_value(value, "object") is value

    def test_object_value_parses_json_string(self):
        """Test that object parameters parse JSON string values."""
        value = '{"attendee": {"name": "Jane"}}'

        assert _coerce_parameter_value(value, "object") == {
            "attendee": {"name": "Jane"}
        }

    def test_array_value_returns_list_unchanged(self):
        """Test that array parameters preserve list values."""
        value = [{"name": "Jane"}, {"name": "Sam"}]

        assert _coerce_parameter_value(value, "array") is value

    def test_array_value_parses_json_string(self):
        """Test that array parameters parse JSON string values."""
        value = '[{"name": "Jane"}, {"name": "Sam"}]'

        assert _coerce_parameter_value(value, "array") == [
            {"name": "Jane"},
            {"name": "Sam"},
        ]

    @pytest.mark.parametrize("value", ["not json", "[]", "null"])
    def test_object_value_rejects_invalid_or_wrong_shape(self, value):
        """Test that object parameters require a JSON object."""
        with pytest.raises(ValueError, match="Cannot convert"):
            _coerce_parameter_value(value, "object")

    @pytest.mark.parametrize("value", ["not json", "{}", "null"])
    def test_array_value_rejects_invalid_or_wrong_shape(self, value):
        """Test that array parameters require a JSON array."""
        with pytest.raises(ValueError, match="Cannot convert"):
            _coerce_parameter_value(value, "array")


class TestTransferResolver:
    """Tests for dynamic transfer resolution behavior."""

    def test_dynamic_transfer_handler_timeout_includes_resolver_budget(self):
        from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager

        manager = CustomToolManager(Mock())
        tool = MockToolModel(
            tool_uuid="transfer-tool-uuid",
            name="Transfer Call",
            description="Transfer the caller",
            category="transfer_call",
            definition={
                "schema_version": 1,
                "type": "transfer_call",
                "config": {
                    "destination_source": "dynamic",
                    "timeout": 120,
                    "resolver": {
                        "type": "http",
                        "url": "https://crm.example.com/resolve-transfer",
                        "timeout_ms": 5000,
                    },
                },
            },
        )

        _handler, timeout_secs = manager._create_handler(tool, "transfer_call")

        assert timeout_secs == 140.0

    @pytest.mark.asyncio
    async def test_http_resolver_resolves_transfer_context_destination(self):
        from api.services.workflow.tools.transfer_resolver import (
            resolve_transfer_config,
        )

        tool = MockToolModel(
            tool_uuid="transfer-tool-uuid",
            name="Transfer Call",
            description="Transfer the caller",
            category="transfer_call",
            definition={},
        )
        config = {
            "destination_source": "dynamic",
            "destination": "",
            "timeout": 30,
            "resolver": {
                "type": "http",
                "url": "https://crm.example.com/resolve-transfer",
                "headers": {"X-Tenant": "ovation"},
                "timeout_ms": 3000,
                "preset_parameters": [
                    {
                        "name": "lead_id",
                        "type": "string",
                        "value_template": "{{initial_context.lead_id}}",
                        "required": True,
                    }
                ],
            },
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "transfer_context": {
                "destination": "+14155550123",
                "custom_message": "I will connect you with our Texas partner now.",
            }
        }

        with (
            patch(
                "api.services.workflow.tools.transfer_resolver.validate_user_configured_service_url"
            ),
            patch(
                "api.services.workflow.tools.transfer_resolver.httpx.AsyncClient"
            ) as mock_client_class,
        ):
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            resolved = await resolve_transfer_config(
                tool=tool,
                config=config,
                arguments={"state": "TX"},
                call_context_vars={"lead_id": "lead-123"},
                gathered_context_vars={},
                organization_id=1,
                workflow_run_id=1,
            )

        assert resolved.destination == "+14155550123"
        assert resolved.message == "I will connect you with our Texas partner now."
        assert resolved.timeout_seconds == 30
        assert resolved.source == "http_resolver"
        mock_client.request.assert_awaited_once()
        request_kwargs = mock_client.request.await_args.kwargs
        assert request_kwargs["method"] == "POST"
        assert request_kwargs["json"] == {"state": "TX", "lead_id": "lead-123"}
        assert request_kwargs["headers"]["X-Tenant"] == "ovation"

    @pytest.mark.asyncio
    async def test_http_resolver_requires_transfer_context_destination(self):
        from api.services.workflow.tools.transfer_resolver import (
            TransferResolutionError,
            resolve_transfer_config,
        )

        tool = MockToolModel(
            tool_uuid="transfer-tool-uuid",
            name="Transfer Call",
            description="Transfer the caller",
            category="transfer_call",
            definition={},
        )
        config = {
            "destination_source": "dynamic",
            "destination": "",
            "timeout": 30,
            "resolver": {
                "type": "http",
                "url": "https://crm.example.com/resolve-transfer",
                "timeout_ms": 3000,
            },
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"transfer_context": {}}

        with (
            patch(
                "api.services.workflow.tools.transfer_resolver.validate_user_configured_service_url"
            ),
            patch(
                "api.services.workflow.tools.transfer_resolver.httpx.AsyncClient"
            ) as mock_client_class,
        ):
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            with pytest.raises(TransferResolutionError) as exc_info:
                await resolve_transfer_config(
                    tool=tool,
                    config=config,
                    arguments={},
                    call_context_vars={},
                    gathered_context_vars={},
                    organization_id=1,
                    workflow_run_id=1,
                )

        assert exc_info.value.reason == "no_destination"


class TestAuthHeaders:
    """Tests for auth header building utilities."""

    def test_bearer_token_auth(self):
        """Test building bearer token auth header."""
        from api.utils.credential_auth import build_auth_header

        mock_credential = Mock()
        mock_credential.credential_type = "bearer_token"
        mock_credential.credential_data = {"token": "abc123"}

        header = build_auth_header(mock_credential)

        assert header == {"Authorization": "Bearer abc123"}

    def test_api_key_auth(self):
        """Test building API key auth header."""
        from api.utils.credential_auth import build_auth_header

        mock_credential = Mock()
        mock_credential.credential_type = "api_key"
        mock_credential.credential_data = {
            "header_name": "X-API-Key",
            "api_key": "secret-key-123",
        }

        header = build_auth_header(mock_credential)

        assert header == {"X-API-Key": "secret-key-123"}

    def test_basic_auth(self):
        """Test building basic auth header."""
        import base64

        from api.utils.credential_auth import build_auth_header

        mock_credential = Mock()
        mock_credential.credential_type = "basic_auth"
        mock_credential.credential_data = {
            "username": "user",
            "password": "pass123",
        }

        header = build_auth_header(mock_credential)

        expected_encoded = base64.b64encode(b"user:pass123").decode()
        assert header == {"Authorization": f"Basic {expected_encoded}"}

    def test_custom_header_auth(self):
        """Test building custom header auth."""
        from api.utils.credential_auth import build_auth_header

        mock_credential = Mock()
        mock_credential.credential_type = "custom_header"
        mock_credential.credential_data = {
            "header_name": "X-Custom-Auth",
            "header_value": "custom-value-123",
        }

        header = build_auth_header(mock_credential)

        assert header == {"X-Custom-Auth": "custom-value-123"}

    def test_unknown_auth_type_returns_empty(self):
        """Test that unknown auth types return empty dict."""
        from api.utils.credential_auth import build_auth_header

        mock_credential = Mock()
        mock_credential.credential_type = "unknown_type"
        mock_credential.credential_data = {}

        header = build_auth_header(mock_credential)

        assert header == {}

    def test_none_credential_type_returns_empty(self):
        """Test that 'none' credential type returns empty dict."""
        from api.utils.credential_auth import build_auth_header

        mock_credential = Mock()
        mock_credential.credential_type = "none"
        mock_credential.credential_data = {}

        header = build_auth_header(mock_credential)

        assert header == {}

    def test_build_auth_header_from_data(self):
        """Test building auth header from raw data."""
        from api.utils.credential_auth import build_auth_header_from_data

        header = build_auth_header_from_data(
            credential_type="bearer_token",
            credential_data={"token": "my-token"},
        )

        assert header == {"Authorization": "Bearer my-token"}

    def test_api_key_default_header_name(self):
        """Test that API key uses default header name if not specified."""
        from api.utils.credential_auth import build_auth_header

        mock_credential = Mock()
        mock_credential.credential_type = "api_key"
        mock_credential.credential_data = {"api_key": "key123"}

        header = build_auth_header(mock_credential)

        assert header == {"X-API-Key": "key123"}


class TestCustomToolManagerIntegration:
    """Integration tests for CustomToolManager with MockLLMService."""

    @pytest.mark.asyncio
    async def test_llm_calls_custom_tool_handler(self):
        """Test that when LLM makes a function call, the custom tool handler is executed."""
        # Create function call chunks that simulate LLM calling a custom tool
        chunks = MockLLMService.create_function_call_chunks(
            function_name="book_appointment",
            arguments={"customer_name": "John Doe", "date": "2024-01-15"},
            tool_call_id="call_custom_123",
        )

        llm = MockLLMService(mock_chunks=chunks, chunk_delay=0.001)

        # Track if our handler was called
        handler_called = False
        received_arguments = None

        async def mock_book_appointment(params: FunctionCallParams):
            nonlocal handler_called, received_arguments
            handler_called = True
            received_arguments = params.arguments
            await params.result_callback({"status": "booked", "confirmation": "ABC123"})

        # Register the function handler
        llm.register_function("book_appointment", mock_book_appointment)

        # Create context and run
        messages = [
            {"role": "user", "content": "Book an appointment for John Doe on Jan 15"}
        ]
        context = LLMContext(messages)

        pipeline = Pipeline([llm])
        frames_to_send = [LLMContextFrame(context)]

        await run_test(
            pipeline,
            frames_to_send=frames_to_send,
            expected_down_frames=[
                LLMServiceMetadataFrame,
                LLMFullResponseStartFrame,
                FunctionCallsFromLLMInfoFrame,
                UserTurnInferenceCompletedFrame,
                FunctionCallsStartedFrame,
                LLMFullResponseEndFrame,
                FunctionCallInProgressFrame,
                FunctionCallResultFrame,
            ],
        )

        # Verify handler was called with correct arguments
        assert handler_called, "Custom tool handler should have been called"
        assert received_arguments == {"customer_name": "John Doe", "date": "2024-01-15"}

    @pytest.mark.asyncio
    async def test_multiple_custom_tools_can_be_registered(self):
        """Test that multiple custom tools can be registered and called."""
        # Create chunks for calling multiple tools
        functions = [
            {
                "name": "get_weather",
                "arguments": {"location": "NYC"},
                "tool_call_id": "call_weather",
            },
            {
                "name": "book_restaurant",
                "arguments": {"restaurant": "Tavern", "party_size": 4},
                "tool_call_id": "call_restaurant",
            },
        ]
        chunks = MockLLMService.create_multiple_function_call_chunks(functions)

        llm = MockLLMService(mock_chunks=chunks, chunk_delay=0.001)

        # Track calls
        calls_made = []

        async def mock_get_weather(params: FunctionCallParams):
            calls_made.append(("get_weather", params.arguments))
            await params.result_callback({"temp": 72, "condition": "sunny"})

        async def mock_book_restaurant(params: FunctionCallParams):
            calls_made.append(("book_restaurant", params.arguments))
            await params.result_callback({"confirmed": True})

        llm.register_function("get_weather", mock_get_weather)
        llm.register_function("book_restaurant", mock_book_restaurant)

        messages = [{"role": "user", "content": "Check weather and book restaurant"}]
        context = LLMContext(messages)

        pipeline = Pipeline([llm])
        await run_test(
            pipeline,
            frames_to_send=[LLMContextFrame(context)],
            expected_down_frames=None,
        )

        # Verify both handlers were called
        assert len(calls_made) == 2
        tool_names = [call[0] for call in calls_made]
        assert "get_weather" in tool_names
        assert "book_restaurant" in tool_names


class TestCustomToolManagerUnit:
    """Unit tests for CustomToolManager class."""

    @pytest.mark.asyncio
    async def test_get_tool_schemas_returns_correct_format(self):
        """Test that get_tool_schemas returns FunctionSchema objects."""
        # Create a mock engine
        from pipecat.adapters.schemas.function_schema import FunctionSchema

        from api.services.workflow.pipecat_engine import PipecatEngine
        from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager

        mock_engine = Mock()
        mock_engine._workflow_run_id = 1
        mock_engine._call_context_vars = {}
        mock_engine._organization_id = None
        mock_engine._get_organization_id = PipecatEngine._get_organization_id.__get__(
            mock_engine
        )

        manager = CustomToolManager(mock_engine)

        # Mock the database client
        mock_tool = MockToolModel(
            tool_uuid="uuid-1",
            name="Test Tool",
            description="A test tool",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "POST",
                    "url": "https://api.example.com/test",
                    "parameters": [
                        {
                            "name": "param1",
                            "type": "string",
                            "description": "Test param",
                            "required": True,
                        }
                    ],
                },
            },
        )

        with (
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.db_client"
            ) as mock_db,
            patch(
                "api.db:db_client.get_organization_id_by_workflow_run_id",
                new_callable=AsyncMock,
                return_value=1,
            ),
        ):
            mock_db.get_tools_by_uuids = AsyncMock(return_value=[mock_tool])

            schemas = await manager.get_tool_schemas(["uuid-1"])

            assert len(schemas) == 1
            schema = schemas[0]

            # Schema should be a FunctionSchema object
            assert isinstance(schema, FunctionSchema)

            # FunctionSchema should have correct attributes
            assert schema.name == "test_tool"
            assert "param1" in schema.properties
            assert schema.properties["param1"]["type"] == "string"
            assert "param1" in schema.required

    @pytest.mark.asyncio
    async def test_register_handlers_creates_working_handler(self):
        """Test that register_handlers creates handlers that can execute tools."""
        from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager

        # Create a mock engine with a mock LLM
        mock_llm = Mock()
        registered_handlers = {}
        registered_kwargs = {}

        def capture_register(name, handler, **kwargs):
            registered_handlers[name] = handler
            registered_kwargs[name] = kwargs

        mock_llm.register_function = capture_register

        from api.services.workflow.pipecat_engine import PipecatEngine

        mock_engine = Mock()
        mock_engine._workflow_run_id = 1
        mock_engine._call_context_vars = {}
        mock_engine._organization_id = None
        mock_engine._get_organization_id = PipecatEngine._get_organization_id.__get__(
            mock_engine
        )
        mock_engine.llm = mock_llm

        manager = CustomToolManager(mock_engine)

        mock_tool = MockToolModel(
            tool_uuid="uuid-1",
            name="API Call",
            description="Make an API call",
            category="http_api",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "POST",
                    "url": "https://api.example.com/call",
                    "parameters": [],
                },
            },
        )

        with (
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.db_client"
            ) as mock_db,
            patch(
                "api.db:db_client.get_organization_id_by_workflow_run_id",
                new_callable=AsyncMock,
                return_value=1,
            ),
        ):
            mock_db.get_tools_by_uuids = AsyncMock(return_value=[mock_tool])

            await manager.register_handlers(["uuid-1"])

            # Verify handler was registered
            assert "api_call" in registered_handlers
            assert registered_kwargs["api_call"]["timeout_secs"] == pytest.approx(5)
            assert registered_kwargs["api_call"]["is_node_transition"] is False

        # Now test that the handler works
        handler = registered_handlers["api_call"]

        result_received = None

        async def mock_result_callback(result, properties=None):
            nonlocal result_received
            result_received = result

        mock_params = Mock()
        mock_params.arguments = {"key": "value"}
        mock_params.result_callback = mock_result_callback

        with patch(
            "api.services.workflow.pipecat_engine_custom_tools.execute_http_tool"
        ) as mock_execute:
            mock_execute.return_value = {
                "status": "success",
                "data": {"response": "ok"},
            }

            await handler(mock_params)

            # Verify execute was called
            mock_execute.assert_called_once()

            # Verify result was returned
            assert result_received["status"] == "success"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("category", ["end_call", "transfer_call"])
    async def test_register_handlers_marks_call_control_tools_as_node_transitions(
        self, category
    ):
        from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager

        mock_engine = Mock()
        mock_engine._get_organization_id = AsyncMock(return_value=1)
        mock_engine.llm.register_function = Mock()
        manager = CustomToolManager(mock_engine)
        tool = MockToolModel(
            tool_uuid=f"{category}-uuid",
            name=category.replace("_", " "),
            description=f"Perform {category}",
            category=category,
            definition={
                "schema_version": 1,
                "type": category,
                "config": {},
            },
        )

        with patch(
            "api.services.workflow.pipecat_engine_custom_tools.db_client.get_tools_by_uuids",
            new=AsyncMock(return_value=[tool]),
        ):
            await manager.register_handlers([tool.tool_uuid])

        mock_engine.llm.register_function.assert_called_once()
        assert (
            mock_engine.llm.register_function.call_args.kwargs["is_node_transition"]
            is True
        )

    @pytest.mark.asyncio
    async def test_transfer_call_renders_destination_from_initial_context(self):
        """Transfer call tools resolve destination templates before provider calls."""
        from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager

        mock_engine = Mock()
        mock_engine._workflow_run_id = 1
        mock_engine._call_context_vars = {
            "transfer_destination": "+14155550123",
        }
        mock_engine._gathered_context = {}
        mock_engine._fetch_recording_audio = None
        mock_engine._audio_config = SimpleNamespace(transport_out_sample_rate=8000)
        mock_engine._transport_output = SimpleNamespace(queue_frame=AsyncMock())
        mock_engine._get_organization_id = AsyncMock(return_value=1)
        mock_engine.set_mute_pipeline = Mock()
        mock_engine.end_call_with_reason = AsyncMock()

        manager = CustomToolManager(mock_engine)
        tool = MockToolModel(
            tool_uuid="transfer-tool-uuid",
            name="Transfer Call",
            description="Transfer the caller",
            category="transfer_call",
            definition={
                "schema_version": 1,
                "type": "transfer_call",
                "config": {
                    "destination": "{{initial_context.transfer_destination}}",
                    "timeout": 30,
                },
            },
        )
        handler, _timeout_secs = manager._create_handler(tool, "transfer_call")

        workflow_run = SimpleNamespace(
            mode=WorkflowRunMode.TWILIO.value,
            gathered_context={"call_id": "caller-call-sid"},
        )
        provider = Mock()
        provider.supports_transfers.return_value = True
        provider.validate_config.return_value = True
        provider.transfer_call = AsyncMock(return_value={"call_sid": "dest-call-sid"})

        transfer_event = Mock()
        transfer_event.to_result_dict.return_value = {
            "status": "failed",
            "action": "transfer_failed",
            "reason": "test_complete",
        }
        transfer_manager = Mock()
        transfer_manager.store_transfer_context = AsyncMock()
        transfer_manager.wait_for_transfer_completion = AsyncMock(
            return_value=transfer_event
        )

        result_received = None

        async def mock_result_callback(result, properties=None):
            nonlocal result_received
            result_received = result

        mock_params = Mock()
        mock_params.arguments = {}
        mock_params.result_callback = mock_result_callback

        with (
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.db_client.get_workflow_run_by_id",
                new=AsyncMock(return_value=workflow_run),
            ),
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.get_telephony_provider_for_run",
                new=AsyncMock(return_value=provider),
            ),
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.get_call_transfer_manager",
                new=AsyncMock(return_value=transfer_manager),
            ),
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.play_audio_loop",
                new=AsyncMock(return_value=None),
            ),
        ):
            await handler(mock_params)

        provider.transfer_call.assert_awaited_once()
        assert provider.transfer_call.await_args.kwargs["destination"] == "+14155550123"
        first_context = transfer_manager.store_transfer_context.await_args_list[0].args[
            0
        ]
        assert first_context.target_number == "+14155550123"
        assert result_received["status"] == "transfer_failed"

    @pytest.mark.asyncio
    async def test_transfer_call_http_resolver_uses_transfer_context_destination(self):
        """HTTP resolver transfer_context.destination is passed to the provider."""
        from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager

        mock_engine = Mock()
        mock_engine._workflow_run_id = 1
        mock_engine._call_context_vars = {}
        mock_engine._gathered_context = {"state": "TX"}
        mock_engine._fetch_recording_audio = None
        mock_engine._audio_config = SimpleNamespace(transport_out_sample_rate=8000)
        mock_engine._transport_output = SimpleNamespace(queue_frame=AsyncMock())
        mock_engine._get_organization_id = AsyncMock(return_value=1)
        mock_engine.task = SimpleNamespace(queue_frame=AsyncMock())
        mock_engine.set_mute_pipeline = Mock()
        mock_engine.end_call_with_reason = AsyncMock()

        manager = CustomToolManager(mock_engine)
        tool = MockToolModel(
            tool_uuid="transfer-tool-uuid",
            name="Transfer Call",
            description="Transfer the caller",
            category="transfer_call",
            definition={
                "schema_version": 1,
                "type": "transfer_call",
                "config": {
                    "destination_source": "dynamic",
                    "destination": "",
                    "timeout": 30,
                    "resolver": {
                        "type": "http",
                        "url": "https://crm.example.com/resolve-transfer",
                        "timeout_ms": 3000,
                        "wait_message": "One moment while I find the right team.",
                        "parameters": [
                            {
                                "name": "state",
                                "type": "string",
                                "description": "State to resolve referral partner",
                                "required": True,
                            }
                        ],
                    },
                },
            },
        )
        handler, _timeout_secs = manager._create_handler(tool, "transfer_call")

        workflow_run = SimpleNamespace(
            mode=WorkflowRunMode.TWILIO.value,
            gathered_context={"call_id": "caller-call-sid"},
        )
        provider = Mock()
        provider.supports_transfers.return_value = True
        provider.validate_config.return_value = True
        provider.transfer_call = AsyncMock(return_value={"call_sid": "dest-call-sid"})

        transfer_event = Mock()
        transfer_event.to_result_dict.return_value = {
            "status": "failed",
            "action": "transfer_failed",
            "reason": "test_complete",
        }
        transfer_manager = Mock()
        transfer_manager.store_transfer_context = AsyncMock()
        transfer_manager.wait_for_transfer_completion = AsyncMock(
            return_value=transfer_event
        )

        result_received = None

        async def mock_result_callback(result, properties=None):
            nonlocal result_received
            result_received = result

        mock_params = Mock()
        mock_params.arguments = {"state": "TX"}
        mock_params.result_callback = mock_result_callback

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "transfer_context": {
                "destination": "+14155550123",
                "custom_message": "I will connect you with our Texas partner now.",
            }
        }

        with (
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.db_client.get_workflow_run_by_id",
                new=AsyncMock(return_value=workflow_run),
            ),
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.get_telephony_provider_for_run",
                new=AsyncMock(return_value=provider),
            ),
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.get_call_transfer_manager",
                new=AsyncMock(return_value=transfer_manager),
            ),
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.play_audio_loop",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "api.services.workflow.tools.transfer_resolver.validate_user_configured_service_url"
            ),
            patch(
                "api.services.workflow.tools.transfer_resolver.httpx.AsyncClient"
            ) as mock_client_class,
        ):
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            await handler(mock_params)

        mock_client.request.assert_awaited_once()
        assert mock_client.request.await_args.kwargs["json"] == {"state": "TX"}
        provider.transfer_call.assert_awaited_once()
        transfer_kwargs = provider.transfer_call.await_args.kwargs
        assert transfer_kwargs["destination"] == "+14155550123"
        assert transfer_kwargs["timeout"] == 30
        assert result_received["status"] == "transfer_failed"
        assert [
            call.args[0] for call in mock_engine.set_mute_pipeline.call_args_list
        ] == [
            True,
            False,
        ]

        spoken_texts = [
            call.args[0].text for call in mock_engine.task.queue_frame.await_args_list
        ]
        assert "One moment while I find the right team." in spoken_texts
        assert "I will connect you with our Texas partner now." in spoken_texts

    @pytest.mark.asyncio
    async def test_transfer_call_resolver_failure_does_not_toggle_pipeline_mute(self):
        """Function-call muting covers resolver execution without pipeline state."""
        from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager

        mock_engine = Mock()
        mock_engine._workflow_run_id = 1
        mock_engine._call_context_vars = {}
        mock_engine._gathered_context = {"state": "TX"}
        mock_engine._fetch_recording_audio = None
        mock_engine._get_organization_id = AsyncMock(return_value=1)
        mock_engine.task = SimpleNamespace(queue_frame=AsyncMock())
        mock_engine.set_mute_pipeline = Mock()
        mock_engine.end_call_with_reason = AsyncMock()

        manager = CustomToolManager(mock_engine)
        tool = MockToolModel(
            tool_uuid="transfer-tool-uuid",
            name="Transfer Call",
            description="Transfer the caller",
            category="transfer_call",
            definition={
                "schema_version": 1,
                "type": "transfer_call",
                "config": {
                    "destination_source": "dynamic",
                    "timeout": 30,
                    "resolver": {
                        "type": "http",
                        "url": "https://crm.example.com/resolve-transfer",
                        "timeout_ms": 3000,
                        "wait_message": "One moment while I find the right team.",
                    },
                },
            },
        )
        handler, _timeout_secs = manager._create_handler(tool, "transfer_call")

        workflow_run = SimpleNamespace(
            mode=WorkflowRunMode.TWILIO.value,
            gathered_context={"call_id": "caller-call-sid"},
        )
        result_received = None

        async def mock_result_callback(result, properties=None):
            nonlocal result_received
            result_received = result

        mock_params = Mock()
        mock_params.arguments = {"state": "TX"}
        mock_params.result_callback = mock_result_callback

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"transfer_context": {}}

        with (
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.db_client.get_workflow_run_by_id",
                new=AsyncMock(return_value=workflow_run),
            ),
            patch(
                "api.services.workflow.tools.transfer_resolver.validate_user_configured_service_url"
            ),
            patch(
                "api.services.workflow.tools.transfer_resolver.httpx.AsyncClient"
            ) as mock_client_class,
        ):
            mock_client = AsyncMock()
            mock_client.request.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            await handler(mock_params)

        assert result_received["status"] == "transfer_failed"
        assert result_received["reason"] == "no_destination"
        mock_engine.set_mute_pipeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_transfer_call_propagates_provider_destination_error(self):
        """Provider-specific destination failures are returned through the tool result."""
        from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager

        mock_engine = Mock()
        mock_engine._workflow_run_id = 1
        mock_engine._call_context_vars = {}
        mock_engine._gathered_context = {}
        mock_engine._fetch_recording_audio = None
        mock_engine._audio_config = SimpleNamespace(transport_out_sample_rate=8000)
        mock_engine._transport_output = SimpleNamespace(queue_frame=AsyncMock())
        mock_engine._get_organization_id = AsyncMock(return_value=1)
        mock_engine.set_mute_pipeline = Mock()
        mock_engine.end_call_with_reason = AsyncMock()

        manager = CustomToolManager(mock_engine)
        tool = MockToolModel(
            tool_uuid="transfer-tool-uuid",
            name="Transfer Call",
            description="Transfer the caller",
            category="transfer_call",
            definition={
                "schema_version": 1,
                "type": "transfer_call",
                "config": {
                    "destination": "provider-specific-destination",
                    "timeout": 30,
                },
            },
        )
        handler, _timeout_secs = manager._create_handler(tool, "transfer_call")

        workflow_run = SimpleNamespace(
            mode=WorkflowRunMode.TWILIO.value,
            gathered_context={"call_id": "caller-call-sid"},
        )
        provider = Mock()
        provider.supports_transfers.return_value = True
        provider.validate_config.return_value = True
        provider.transfer_call = AsyncMock(
            side_effect=Exception("provider rejected destination")
        )

        transfer_manager = Mock()
        transfer_manager.store_transfer_context = AsyncMock()
        transfer_manager.remove_transfer_context = AsyncMock()

        result_received = None

        async def mock_result_callback(result, properties=None):
            nonlocal result_received
            result_received = result

        mock_params = Mock()
        mock_params.arguments = {}
        mock_params.result_callback = mock_result_callback

        with (
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.db_client.get_workflow_run_by_id",
                new=AsyncMock(return_value=workflow_run),
            ),
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.get_telephony_provider_for_run",
                new=AsyncMock(return_value=provider),
            ),
            patch(
                "api.services.workflow.pipecat_engine_custom_tools.get_call_transfer_manager",
                new=AsyncMock(return_value=transfer_manager),
            ),
        ):
            await handler(mock_params)

        provider.transfer_call.assert_awaited_once()
        assert (
            provider.transfer_call.await_args.kwargs["destination"]
            == "provider-specific-destination"
        )
        transfer_manager.remove_transfer_context.assert_awaited_once()
        assert result_received == {
            "status": "transfer_failed",
            "reason": "provider_error",
            "message": "Transfer provider failed: provider rejected destination",
        }


def _update_llm_context(context, system_message, functions):
    """Inline helper replicating the old update_llm_context for tests."""
    tools_schema = ToolsSchema(standard_tools=functions)
    previous_interactions = context.messages

    if previous_interactions and previous_interactions[0]["role"] == "system":
        messages = [system_message] + previous_interactions[1:]
    else:
        messages = [system_message] + previous_interactions

    context.set_messages(messages)

    if functions:
        context.set_tools(tools_schema)


class TestUpdateLLMContext:
    """Tests for _update_llm_context inline logic."""

    def test_replaces_system_message(self):
        """Test that _update_llm_context replaces existing system messages."""
        context = LLMContext()
        context.set_messages(
            [
                {"role": "system", "content": "Old system message"},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ]
        )

        new_system = {"role": "system", "content": "New system message"}
        _update_llm_context(context, new_system, [])

        messages = context.messages
        # Should have new system message at the start
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "New system message"
        # Should preserve user and assistant messages
        assert len(messages) == 3
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"

    def test_preserves_conversation_history(self):
        """Test that user/assistant messages are preserved in order."""
        context = LLMContext()
        context.set_messages(
            [
                {"role": "system", "content": "Old prompt"},
                {"role": "user", "content": "First question"},
                {"role": "assistant", "content": "First answer"},
                {"role": "user", "content": "Second question"},
                {"role": "assistant", "content": "Second answer"},
            ]
        )

        new_system = {"role": "system", "content": "New prompt"}
        _update_llm_context(context, new_system, [])

        messages = context.messages
        assert len(messages) == 5
        assert messages[1]["content"] == "First question"
        assert messages[2]["content"] == "First answer"
        assert messages[3]["content"] == "Second question"
        assert messages[4]["content"] == "Second answer"

    def test_sets_tools_when_functions_provided(self):
        """Test that tools are set on context when functions are provided."""
        context = LLMContext()
        context.set_messages([{"role": "system", "content": "Old"}])

        # Create function schemas
        functions = [
            get_function_schema("book_appointment", "Book an appointment"),
            get_function_schema("cancel_appointment", "Cancel an appointment"),
        ]

        new_system = {"role": "system", "content": "New prompt with tools"}
        _update_llm_context(context, new_system, functions)

        # Verify tools were set
        tools = context.tools
        assert tools is not None
        assert len(tools.standard_tools) == 2

    def test_does_not_set_tools_when_functions_empty(self):
        """Test that tools are not set when functions list is empty."""
        context = LLMContext()
        context.set_messages([{"role": "system", "content": "Old"}])

        new_system = {"role": "system", "content": "New prompt without tools"}
        _update_llm_context(context, new_system, [])

        # Tools should not be set (or remain None)
        # Note: The function only calls set_tools if functions is truthy
        # So we verify the context state is as expected
        messages = context.messages
        assert len(messages) == 1
        assert messages[0]["content"] == "New prompt without tools"

    def test_works_with_empty_context(self):
        """Test that update works on a fresh context with no messages."""
        context = LLMContext()

        new_system = {"role": "system", "content": "Initial prompt"}
        functions = [get_function_schema("test_func", "A test function")]

        _update_llm_context(context, new_system, functions)

        messages = context.messages
        assert len(messages) == 1
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "Initial prompt"

    def test_function_schema_structure(self):
        """Test that get_function_schema creates correct structure."""
        schema = get_function_schema(
            "search_products",
            "Search for products in the catalog",
            properties={
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results"},
            },
            required=["query"],
        )

        assert schema.name == "search_products"
        assert schema.description == "Search for products in the catalog"
        assert "query" in schema.properties
        assert "limit" in schema.properties
        assert "query" in schema.required
        assert "limit" not in schema.required

    def test_function_schema_with_no_parameters(self):
        """Test get_function_schema with no properties or required."""
        schema = get_function_schema("ping", "Check if service is alive")

        assert schema.name == "ping"
        assert schema.description == "Check if service is alive"
        assert schema.properties == {}
        assert schema.required == []
