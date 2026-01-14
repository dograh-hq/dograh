"""Custom tool management for PipecatEngine.

This module handles fetching, registering, and executing user-defined tools
during workflow execution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

from api.db import db_client
from api.enums import ToolCategory
from api.services.workflow.disposition_mapper import (
    get_organization_id_from_workflow_run,
)
from api.services.workflow.pipecat_engine_utils import get_function_schema
from api.services.workflow.tools.custom_tool import (
    execute_http_tool,
    tool_to_function_schema,
)
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import FunctionCallResultProperties, TTSSpeakFrame
from pipecat.services.llm_service import FunctionCallParams

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine import PipecatEngine


# End task reason for end call tool
END_CALL_TOOL_REASON = "end_call_tool"


class CustomToolManager:
    """Manager for custom tool registration and execution.

    This class handles:
      1. Fetching tools from the database based on tool UUIDs
      2. Converting tools to LLM function schemas
      3. Registering tool execution handlers with the LLM
      4. Executing tools when invoked by the LLM
    """

    def __init__(self, engine: "PipecatEngine") -> None:
        self._engine = engine
        self._organization_id: Optional[int] = None

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
        """Create a handler function for a tool based on its category.

        Args:
            tool: The ToolModel instance
            function_name: The function name used by the LLM

        Returns:
            Async handler function for the tool
        """
        if tool.category == ToolCategory.END_CALL.value:
            return self._create_end_call_handler(tool, function_name)

        return self._create_http_tool_handler(tool, function_name)

    def _create_http_tool_handler(self, tool: Any, function_name: str):
        """Create a handler function for an HTTP API tool.

        Args:
            tool: The ToolModel instance
            function_name: The function name used by the LLM

        Returns:
            Async handler function for the HTTP API tool
        """
        # Run LLM after tool execution to continue conversation
        properties = FunctionCallResultProperties(run_llm=True)

        async def http_tool_handler(
            function_call_params: FunctionCallParams,
        ) -> None:
            logger.info(f"HTTP Tool EXECUTED: {function_name}")
            logger.info(f"Arguments: {function_call_params.arguments}")

            try:
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
                logger.error(f"HTTP tool '{function_name}' execution failed: {e}")
                await function_call_params.result_callback(
                    {"status": "error", "error": str(e)},
                    properties=properties,
                )

        return http_tool_handler

    def _create_end_call_handler(self, tool: Any, function_name: str):
        """Create a handler function for an end call tool.

        Args:
            tool: The ToolModel instance
            function_name: The function name used by the LLM

        Returns:
            Async handler function for the end call tool
        """
        # Don't run LLM after end call - we're terminating
        properties = FunctionCallResultProperties(run_llm=False)

        async def end_call_handler(
            function_call_params: FunctionCallParams,
        ) -> None:
            logger.info(f"End Call Tool EXECUTED: {function_name}")

            try:
                # Get the end call configuration
                config = tool.definition.get("config", {})
                message_type = config.get("messageType", "none")
                custom_message = config.get("customMessage", "")

                # Send result callback first
                await function_call_params.result_callback(
                    {"status": "success", "action": "ending_call"},
                    properties=properties,
                )

                if message_type == "custom" and custom_message:
                    # Queue the custom message to be spoken
                    logger.info(f"Playing custom goodbye message: {custom_message}")
                    await self._engine.task.queue_frame(TTSSpeakFrame(custom_message))
                    # End the call after the message (not immediately)
                    await self._engine.send_end_task_frame(
                        END_CALL_TOOL_REASON, abort_immediately=False
                    )
                else:
                    # No message - end call immediately
                    logger.info("Ending call immediately (no goodbye message)")
                    await self._engine.send_end_task_frame(
                        END_CALL_TOOL_REASON, abort_immediately=True
                    )

            except Exception as e:
                logger.error(f"End call tool '{function_name}' execution failed: {e}")
                # Still try to end the call even if there's an error
                await self._engine.send_end_task_frame(
                    END_CALL_TOOL_REASON, abort_immediately=True
                )

        return end_call_handler
