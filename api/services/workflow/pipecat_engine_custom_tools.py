"""Custom tool management for PipecatEngine.

This module handles fetching, registering, and executing user-defined tools
during workflow execution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

from api.db import db_client
from api.services.workflow.disposition_mapper import (
    get_organization_id_from_workflow_run,
)
from api.services.workflow.pipecat_engine_utils import get_function_schema
from api.services.workflow.tools.custom_tool import (
    execute_http_tool,
    tool_to_function_schema,
)
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import FunctionCallResultProperties
from pipecat.services.llm_service import FunctionCallParams

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine import PipecatEngine


class CustomToolManager:
    """Manager for custom tool registration and execution.

    This class handles:
      1. Fetching tools from the database based on tool UUIDs
      2. Converting tools to LLM function schemas
      3. Registering tool execution handlers with the LLM
      4. Executing HTTP API tools when invoked by the LLM
    """

    def __init__(self, engine: "PipecatEngine") -> None:
        self._engine = engine
        self._organization_id: Optional[int] = None
        # Cache: maps function_name -> (tool, schema)
        self._tools_cache: dict[str, tuple[Any, dict]] = {}

    async def get_organization_id(self) -> Optional[int]:
        """Get and cache the organization ID from workflow run."""
        if self._organization_id is None:
            self._organization_id = await get_organization_id_from_workflow_run(
                self._engine._workflow_run_id
            )
        return self._organization_id

    async def get_tool_schemas(self, tool_uuids: list[str]) -> list[FunctionSchema]:
        """Fetch custom tools and convert them to function schemas.

        Args:
            tool_uuids: List of tool UUIDs to fetch

        Returns:
            List of FunctionSchema objects for LLM
        """
        organization_id = await self.get_organization_id()
        if not organization_id:
            logger.warning("Cannot fetch custom tools: organization_id not available")
            return []

        try:
            tools = await db_client.get_tools_by_uuids(tool_uuids, organization_id)

            schemas: list[FunctionSchema] = []
            for tool in tools:
                raw_schema = tool_to_function_schema(tool)
                function_name = raw_schema["function"]["name"]

                # Cache the tool for later execution
                self._tools_cache[function_name] = (tool, raw_schema)

                # Convert to FunctionSchema object for compatibility with update_llm_context
                func_schema = get_function_schema(
                    function_name,
                    raw_schema["function"]["description"],
                    properties=raw_schema["function"]["parameters"].get(
                        "properties", {}
                    ),
                    required=raw_schema["function"]["parameters"].get("required", []),
                )
                schemas.append(func_schema)

            logger.debug(
                f"Loaded {len(schemas)} custom tools for node: "
                f"{[s.name for s in schemas]}"
            )
            return schemas

        except Exception as e:
            logger.error(f"Failed to fetch custom tools: {e}")
            return []

    async def register_handlers(self, tool_uuids: list[str]) -> None:
        """Register custom tool execution handlers with the LLM.

        Args:
            tool_uuids: List of tool UUIDs to register handlers for
        """
        organization_id = await self.get_organization_id()
        if not organization_id:
            logger.warning(
                "Cannot register custom tool handlers: organization_id not available"
            )
            return

        try:
            tools = await db_client.get_tools_by_uuids(tool_uuids, organization_id)

            for tool in tools:
                schema = tool_to_function_schema(tool)
                function_name = schema["function"]["name"]

                # Cache the tool for potential later use
                self._tools_cache[function_name] = (tool, schema)

                # Create and register the handler
                handler = self._create_handler(tool, function_name)
                self._engine.llm.register_function(function_name, handler)

                logger.debug(
                    f"Registered custom tool handler: {function_name} "
                    f"(tool_uuid: {tool.tool_uuid})"
                )

        except Exception as e:
            logger.error(f"Failed to register custom tool handlers: {e}")

    def _create_handler(self, tool: Any, function_name: str):
        """Create a handler function for a custom tool.

        Args:
            tool: The ToolModel instance
            function_name: The function name used by the LLM

        Returns:
            Async handler function for the tool
        """
        # Run LLM after tool execution to continue conversation
        properties = FunctionCallResultProperties(run_llm=True)

        async def custom_tool_handler(
            function_call_params: FunctionCallParams,
        ) -> None:
            logger.info(f"LLM Function Call EXECUTED: {function_name}")
            logger.info(f"Arguments: {function_call_params.arguments}")

            try:
                # Execute the HTTP API tool
                result = await execute_http_tool(
                    tool=tool,
                    arguments=function_call_params.arguments,
                    call_context_vars=self._engine._call_context_vars,
                    organization_id=self._organization_id,
                )

                await function_call_params.result_callback(
                    result, properties=properties
                )

            except Exception as e:
                logger.error(f"Custom tool '{function_name}' execution failed: {e}")
                await function_call_params.result_callback(
                    {"status": "error", "error": str(e)},
                    properties=properties,
                )

        return custom_tool_handler

    def get_cached_tool(self, function_name: str) -> Optional[tuple[Any, dict]]:
        """Get a cached tool by its function name.

        Args:
            function_name: The function name used by the LLM

        Returns:
            Tuple of (tool, schema) if found, None otherwise
        """
        return self._tools_cache.get(function_name)

    def clear_cache(self) -> None:
        """Clear the tools cache."""
        self._tools_cache.clear()
