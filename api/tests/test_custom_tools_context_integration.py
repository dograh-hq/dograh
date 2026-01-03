"""Integration tests for CustomToolManager with update_llm_context.

This module tests the full flow of:
1. CustomToolManager fetching and converting tool schemas
2. update_llm_context setting those tools on the LLM context
3. Verifying the context is properly configured for LLM generation
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager
from api.services.workflow.pipecat_engine_utils import (
    get_function_schema,
    update_llm_context,
)
from api.tests.conftest import MockToolModel
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.processors.aggregators.llm_context import LLMContext


class TestCustomToolManagerContextIntegration:
    """Integration tests for CustomToolManager with LLMContext."""

    @pytest.mark.asyncio
    async def test_get_tool_schemas_and_update_context(self, mock_engine, sample_tools):
        """Test fetching tool schemas via CustomToolManager and updating LLM context."""
        manager = CustomToolManager(mock_engine)

        with patch(
            "api.services.workflow.pipecat_engine_custom_tools.get_organization_id_from_workflow_run"
        ) as mock_get_org:
            mock_get_org.return_value = 1

            with patch(
                "api.services.workflow.pipecat_engine_custom_tools.db_client"
            ) as mock_db:
                mock_db.get_tools_by_uuids = AsyncMock(return_value=sample_tools)

                # Get tool schemas via CustomToolManager - now returns FunctionSchema objects
                tool_uuids = ["weather-uuid-123", "booking-uuid-456", "lookup-uuid-789"]
                schemas = await manager.get_tool_schemas(tool_uuids)

                # Verify schemas were returned as FunctionSchema objects
                assert len(schemas) == 3
                assert all(isinstance(s, FunctionSchema) for s in schemas)

                # Create context with conversation history
                context = LLMContext()
                context.set_messages(
                    [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {
                            "role": "user",
                            "content": "I need to check the weather and book an appointment.",
                        },
                        {
                            "role": "assistant",
                            "content": "I can help with both. Where would you like to check the weather?",
                        },
                        {"role": "user", "content": "San Francisco"},
                    ]
                )

                # Update context with new system message and tools
                # Now we can pass schemas directly since they're FunctionSchema objects
                new_system = {
                    "role": "system",
                    "content": "You are a scheduling assistant with access to weather and booking tools.",
                }
                update_llm_context(context, new_system, schemas)

                # Verify context was updated correctly
                messages = context.messages
                assert len(messages) == 4
                assert (
                    messages[0]["content"]
                    == "You are a scheduling assistant with access to weather and booking tools."
                )
                assert messages[1]["role"] == "user"
                assert messages[3]["content"] == "San Francisco"

                # Verify tools were set
                tools = context.tools
                assert tools is not None
                assert len(tools.standard_tools) == 3

                # Verify tool names
                tool_names = {t.name for t in tools.standard_tools}
                assert tool_names == {
                    "get_weather",
                    "book_appointment",
                    "customer_lookup",
                }

    @pytest.mark.asyncio
    async def test_tool_schemas_have_correct_properties(
        self, mock_engine, sample_tools
    ):
        """Test that tool schemas from CustomToolManager have correct parameter properties."""
        manager = CustomToolManager(mock_engine)

        with patch(
            "api.services.workflow.pipecat_engine_custom_tools.get_organization_id_from_workflow_run"
        ) as mock_get_org:
            mock_get_org.return_value = 1

            with patch(
                "api.services.workflow.pipecat_engine_custom_tools.db_client"
            ) as mock_db:
                mock_db.get_tools_by_uuids = AsyncMock(return_value=sample_tools)

                schemas = await manager.get_tool_schemas(
                    ["weather-uuid-123", "booking-uuid-456"]
                )

                # Find the booking schema - now using FunctionSchema attributes
                booking_schema = next(
                    s for s in schemas if s.name == "book_appointment"
                )

                # Verify parameter properties
                assert "customer_name" in booking_schema.properties
                assert "date" in booking_schema.properties
                assert "time" in booking_schema.properties
                assert "notes" in booking_schema.properties

                # Verify types
                assert booking_schema.properties["customer_name"]["type"] == "string"
                assert booking_schema.properties["date"]["type"] == "string"

                # Verify required
                assert "customer_name" in booking_schema.required
                assert "date" in booking_schema.required
                assert "time" in booking_schema.required
                assert "notes" not in booking_schema.required

    @pytest.mark.asyncio
    async def test_context_update_with_builtin_and_custom_tools(
        self, mock_engine, sample_tools
    ):
        """Test updating context with both built-in and custom tools."""
        manager = CustomToolManager(mock_engine)

        with patch(
            "api.services.workflow.pipecat_engine_custom_tools.get_organization_id_from_workflow_run"
        ) as mock_get_org:
            mock_get_org.return_value = 1

            with patch(
                "api.services.workflow.pipecat_engine_custom_tools.db_client"
            ) as mock_db:
                mock_db.get_tools_by_uuids = AsyncMock(
                    return_value=[sample_tools[0]]
                )  # Just weather

                # Get custom tool schemas - returns FunctionSchema objects
                custom_schemas = await manager.get_tool_schemas(["weather-uuid-123"])

                # Create built-in function schemas (like calculator, timezone)
                builtin_functions = [
                    get_function_schema(
                        "safe_calculator",
                        "Evaluate a mathematical expression safely",
                        properties={
                            "expression": {
                                "type": "string",
                                "description": "Mathematical expression to evaluate",
                            }
                        },
                        required=["expression"],
                    ),
                    get_function_schema(
                        "get_current_time",
                        "Get the current time in a timezone",
                        properties={
                            "timezone": {
                                "type": "string",
                                "description": "Timezone name (e.g., America/New_York)",
                            }
                        },
                        required=["timezone"],
                    ),
                ]

                # Combine built-in and custom functions - both are FunctionSchema objects
                all_functions = builtin_functions + custom_schemas

                # Update context
                context = LLMContext()
                context.set_messages([{"role": "system", "content": "Old prompt"}])

                new_system = {
                    "role": "system",
                    "content": "Assistant with calculator and weather tools",
                }
                update_llm_context(context, new_system, all_functions)

                # Verify all tools are present
                tools = context.tools
                assert len(tools.standard_tools) == 3

                tool_names = {t.name for t in tools.standard_tools}
                assert "safe_calculator" in tool_names
                assert "get_current_time" in tool_names
                assert "get_weather" in tool_names

    @pytest.mark.asyncio
    async def test_tools_cached_after_first_fetch(self, mock_engine, sample_tools):
        """Test that CustomToolManager caches tools after first fetch."""
        manager = CustomToolManager(mock_engine)

        with patch(
            "api.services.workflow.pipecat_engine_custom_tools.get_organization_id_from_workflow_run"
        ) as mock_get_org:
            mock_get_org.return_value = 1

            with patch(
                "api.services.workflow.pipecat_engine_custom_tools.db_client"
            ) as mock_db:
                mock_db.get_tools_by_uuids = AsyncMock(return_value=[sample_tools[0]])

                # First fetch
                await manager.get_tool_schemas(["weather-uuid-123"])

                # Verify tool is cached (cache stores raw schema dict, not FunctionSchema)
                cached = manager.get_cached_tool("get_weather")
                assert cached is not None
                tool, raw_schema = cached
                assert tool.tool_uuid == "weather-uuid-123"
                assert raw_schema["function"]["name"] == "get_weather"

    @pytest.mark.asyncio
    async def test_context_preserves_function_call_history(
        self, mock_engine, sample_tools
    ):
        """Test that update_llm_context preserves function call messages in history."""
        manager = CustomToolManager(mock_engine)

        with patch(
            "api.services.workflow.pipecat_engine_custom_tools.get_organization_id_from_workflow_run"
        ) as mock_get_org:
            mock_get_org.return_value = 1

            with patch(
                "api.services.workflow.pipecat_engine_custom_tools.db_client"
            ) as mock_db:
                mock_db.get_tools_by_uuids = AsyncMock(return_value=[sample_tools[0]])

                # Get schemas - returns FunctionSchema objects
                schemas = await manager.get_tool_schemas(["weather-uuid-123"])

                # Create context with function call history
                context = LLMContext()
                context.set_messages(
                    [
                        {"role": "system", "content": "Old system prompt"},
                        {"role": "user", "content": "What's the weather in NYC?"},
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_123",
                                    "type": "function",
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": '{"location": "New York, NY"}',
                                    },
                                }
                            ],
                        },
                        {
                            "role": "tool",
                            "tool_call_id": "call_123",
                            "content": '{"temperature": 72, "condition": "sunny"}',
                        },
                        {
                            "role": "assistant",
                            "content": "The weather in NYC is 72°F and sunny!",
                        },
                    ]
                )

                new_system = {"role": "system", "content": "Updated weather assistant"}
                update_llm_context(context, new_system, schemas)

                messages = context.messages
                # System + user + assistant(tool_call) + tool + assistant = 5
                assert len(messages) == 5

                # Verify function call messages are preserved
                tool_call_msg = messages[2]
                assert tool_call_msg["role"] == "assistant"
                assert "tool_calls" in tool_call_msg

                tool_result_msg = messages[3]
                assert tool_result_msg["role"] == "tool"
                assert tool_result_msg["tool_call_id"] == "call_123"

    @pytest.mark.asyncio
    async def test_empty_tool_list_does_not_set_tools(self, mock_engine):
        """Test that empty tool list doesn't set tools on context."""
        manager = CustomToolManager(mock_engine)

        with patch(
            "api.services.workflow.pipecat_engine_custom_tools.get_organization_id_from_workflow_run"
        ) as mock_get_org:
            mock_get_org.return_value = 1

            with patch(
                "api.services.workflow.pipecat_engine_custom_tools.db_client"
            ) as mock_db:
                mock_db.get_tools_by_uuids = AsyncMock(return_value=[])

                schemas = await manager.get_tool_schemas([])
                assert schemas == []

                context = LLMContext()
                context.set_messages([{"role": "system", "content": "Old"}])

                new_system = {"role": "system", "content": "No tools available"}
                update_llm_context(context, new_system, [])

                # Context should have updated message but no tools set
                assert context.messages[0]["content"] == "No tools available"

    @pytest.mark.asyncio
    async def test_numeric_and_boolean_parameter_types(self, mock_engine):
        """Test that numeric and boolean parameter types are correctly handled."""
        tool_with_types = MockToolModel(
            tool_uuid="order-uuid",
            name="Place Order",
            description="Place an order for items",
            definition={
                "schema_version": 1,
                "type": "http_api",
                "config": {
                    "method": "POST",
                    "url": "https://api.example.com/orders",
                    "parameters": [
                        {
                            "name": "item_id",
                            "type": "string",
                            "description": "Item identifier",
                            "required": True,
                        },
                        {
                            "name": "quantity",
                            "type": "number",
                            "description": "Number of items",
                            "required": True,
                        },
                        {
                            "name": "express_shipping",
                            "type": "boolean",
                            "description": "Use express shipping",
                            "required": False,
                        },
                    ],
                },
            },
        )

        manager = CustomToolManager(mock_engine)

        with patch(
            "api.services.workflow.pipecat_engine_custom_tools.get_organization_id_from_workflow_run"
        ) as mock_get_org:
            mock_get_org.return_value = 1

            with patch(
                "api.services.workflow.pipecat_engine_custom_tools.db_client"
            ) as mock_db:
                mock_db.get_tools_by_uuids = AsyncMock(return_value=[tool_with_types])

                # Get schemas - returns FunctionSchema objects
                schemas = await manager.get_tool_schemas(["order-uuid"])
                schema = schemas[0]

                # Verify types using FunctionSchema attributes
                assert schema.properties["item_id"]["type"] == "string"
                assert schema.properties["quantity"]["type"] == "number"
                assert schema.properties["express_shipping"]["type"] == "boolean"

                # Update context - pass schema directly
                context = LLMContext()
                context.set_messages([{"role": "system", "content": "Old"}])
                update_llm_context(
                    context, {"role": "system", "content": "Order assistant"}, schemas
                )

                # Verify tool was set with correct types
                tool = context.tools.standard_tools[0]
                assert tool.name == "place_order"
                assert tool.properties["quantity"]["type"] == "number"
                assert tool.properties["express_shipping"]["type"] == "boolean"
