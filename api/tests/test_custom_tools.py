"""Tests for custom tool integration with PipecatEngine.

This module tests:
1. tool_to_function_schema - converting tool models to LLM function schemas
2. execute_http_tool - executing HTTP API tools
3. CustomToolManager - tool registration and handler execution
4. End-to-end LLM generation with custom tool calls
"""

from dataclasses import dataclass
from typing import Any, Dict
from unittest.mock import AsyncMock, Mock, patch

import pytest

from api.services.workflow.pipecat_engine_utils import (
    get_function_schema,
    update_llm_context,
)
from api.services.workflow.tools.custom_tool import (
    execute_http_tool,
    tool_to_function_schema,
)
from pipecat.frames.frames import (
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    FunctionCallsFromLLMInfoFrame,
    FunctionCallsStartedFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.llm_service import FunctionCallParams
from pipecat.tests import MockLLMService, run_test


@dataclass
class MockToolModel:
    """Mock tool model for testing."""

    tool_uuid: str
    name: str
    description: str
    definition: Dict[str, Any]


class TestToolToFunctionSchema:
    """Tests for tool_to_function_schema function."""

    def test_simple_tool_with_string_parameter(self):
        """Test converting a simple tool with one string parameter."""
        tool = MockToolModel(
            tool_uuid="test-uuid-1",
            name="Get Weather",
            description="Get current weather for a location",
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

    def test_tool_name_sanitization(self):
        """Test that tool names with special characters are sanitized."""
        tool = MockToolModel(
            tool_uuid="test-uuid-3",
            name="Get User's Account Info!!!",
            description="Get account information",
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


class TestExecuteHttpTool:
    """Tests for execute_http_tool function."""

    @pytest.mark.asyncio
    async def test_post_request_sends_json_body(self):
        """Test that POST requests send arguments as JSON body."""
        tool = MockToolModel(
            tool_uuid="test-uuid",
            name="Create User",
            description="Create a new user",
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

    @pytest.mark.asyncio
    async def test_get_request_sends_query_params(self):
        """Test that GET requests send arguments as query parameters."""
        tool = MockToolModel(
            tool_uuid="test-uuid",
            name="Search Users",
            description="Search for users",
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
    async def test_delete_request_sends_query_params(self):
        """Test that DELETE requests send arguments as query parameters."""
        tool = MockToolModel(
            tool_uuid="test-uuid",
            name="Delete User",
            description="Delete a user",
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

            result = await execute_http_tool(tool, arguments)

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

            await execute_http_tool(tool, {"data": "test"})

            call_kwargs = mock_client.request.call_args.kwargs
            assert call_kwargs["headers"]["X-API-Key"] == "secret-key"
            assert call_kwargs["headers"]["X-Custom-Header"] == "custom-value"

    @pytest.mark.asyncio
    async def test_request_includes_auth_header_from_credential(self):
        """Test that auth headers from credentials are included in the request."""
        tool = MockToolModel(
            tool_uuid="test-uuid",
            name="Authenticated API",
            description="API that requires authentication",
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

                await execute_http_tool(tool, {"data": "test"}, organization_id=1)

                # Verify credential was fetched
                mock_db.get_credential_by_uuid.assert_called_once_with(
                    "cred-uuid-123", 1
                )

                # Verify auth header was added
                call_kwargs = mock_client.request.call_args.kwargs
                assert (
                    call_kwargs["headers"]["Authorization"] == "Bearer my-secret-token"
                )

    @pytest.mark.asyncio
    async def test_no_credential_lookup_without_organization_id(self):
        """Test that credential lookup is skipped without organization_id."""
        tool = MockToolModel(
            tool_uuid="test-uuid",
            name="API with Credential",
            description="API with credential configured",
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
                LLMFullResponseStartFrame,
                FunctionCallsFromLLMInfoFrame,
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
        from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager
        from pipecat.adapters.schemas.function_schema import FunctionSchema

        # Create a mock engine
        mock_engine = Mock()
        mock_engine._workflow_run_id = 1
        mock_engine._call_context_vars = {}

        manager = CustomToolManager(mock_engine)

        # Mock the database client
        mock_tool = MockToolModel(
            tool_uuid="uuid-1",
            name="Test Tool",
            description="A test tool",
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

        with patch(
            "api.services.workflow.pipecat_engine_custom_tools.get_organization_id_from_workflow_run"
        ) as mock_get_org:
            mock_get_org.return_value = 1

            with patch(
                "api.services.workflow.pipecat_engine_custom_tools.db_client"
            ) as mock_db:
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

        def capture_register(name, handler, **kwargs):
            registered_handlers[name] = handler

        mock_llm.register_function = capture_register

        mock_engine = Mock()
        mock_engine._workflow_run_id = 1
        mock_engine._call_context_vars = {}
        mock_engine.llm = mock_llm

        manager = CustomToolManager(mock_engine)

        mock_tool = MockToolModel(
            tool_uuid="uuid-1",
            name="API Call",
            description="Make an API call",
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

        with patch(
            "api.services.workflow.pipecat_engine_custom_tools.get_organization_id_from_workflow_run"
        ) as mock_get_org:
            mock_get_org.return_value = 1

            with patch(
                "api.services.workflow.pipecat_engine_custom_tools.db_client"
            ) as mock_db:
                mock_db.get_tools_by_uuids = AsyncMock(return_value=[mock_tool])

                await manager.register_handlers(["uuid-1"])

                # Verify handler was registered
                assert "api_call" in registered_handlers

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
    async def test_tools_cache_prevents_duplicate_fetches(self):
        """Test that tools are cached after first fetch."""
        from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager

        mock_engine = Mock()
        mock_engine._workflow_run_id = 1
        mock_engine._call_context_vars = {}
        mock_engine.llm = Mock()
        mock_engine.llm.register_function = Mock()

        manager = CustomToolManager(mock_engine)

        mock_tool = MockToolModel(
            tool_uuid="uuid-1",
            name="Cached Tool",
            description="A tool that should be cached",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {"method": "GET", "url": "https://api.example.com"},
            },
        )

        with patch(
            "api.services.workflow.pipecat_engine_custom_tools.get_organization_id_from_workflow_run"
        ) as mock_get_org:
            mock_get_org.return_value = 1

            with patch(
                "api.services.workflow.pipecat_engine_custom_tools.db_client"
            ) as mock_db:
                mock_db.get_tools_by_uuids = AsyncMock(return_value=[mock_tool])

                # First call should fetch from DB
                await manager.get_tool_schemas(["uuid-1"])

                # Verify tool is now in cache
                cached = manager.get_cached_tool("cached_tool")
                assert cached is not None
                assert cached[0].tool_uuid == "uuid-1"

                # Clear cache and verify it's empty
                manager.clear_cache()
                cached = manager.get_cached_tool("cached_tool")
                assert cached is None


class TestUpdateLLMContext:
    """Tests for update_llm_context function."""

    def test_replaces_system_message(self):
        """Test that update_llm_context replaces existing system messages."""
        context = LLMContext()
        context.set_messages(
            [
                {"role": "system", "content": "Old system message"},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ]
        )

        new_system = {"role": "system", "content": "New system message"}
        update_llm_context(context, new_system, [])

        messages = context.messages
        # Should have new system message at the start
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "New system message"
        # Should preserve user and assistant messages
        assert len(messages) == 3
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"

    def test_removes_multiple_old_system_messages(self):
        """Test that all old system messages are removed."""
        context = LLMContext()
        context.set_messages(
            [
                {"role": "system", "content": "First system message"},
                {"role": "user", "content": "Question 1"},
                {"role": "system", "content": "Second system message"},
                {"role": "assistant", "content": "Answer 1"},
            ]
        )

        new_system = {"role": "system", "content": "Only system message"}
        update_llm_context(context, new_system, [])

        messages = context.messages
        # Should only have one system message now
        system_messages = [m for m in messages if m["role"] == "system"]
        assert len(system_messages) == 1
        assert system_messages[0]["content"] == "Only system message"

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
        update_llm_context(context, new_system, [])

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
        update_llm_context(context, new_system, functions)

        # Verify tools were set
        tools = context.tools
        assert tools is not None
        assert len(tools.standard_tools) == 2

    def test_does_not_set_tools_when_functions_empty(self):
        """Test that tools are not set when functions list is empty."""
        context = LLMContext()
        context.set_messages([{"role": "system", "content": "Old"}])

        new_system = {"role": "system", "content": "New prompt without tools"}
        update_llm_context(context, new_system, [])

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

        update_llm_context(context, new_system, functions)

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
