"""Custom tool management for PipecatEngine.

This module handles fetching, registering, and executing user-defined tools
during workflow execution.
"""

from __future__ import annotations

import asyncio
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
from api.services.workflow.transfer_event_protocol import (
    TransferEventType,
    wait_for_transfer_signal,
)
from api.utils.hold_audio import get_hold_audio_duration_ms, load_hold_audio
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import (
    FunctionCallResultProperties,
    OutputAudioRawFrame,
    TTSSpeakFrame,
)
from pipecat.services.llm_service import FunctionCallParams
from pipecat.utils.enums import EndTaskReason

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine import PipecatEngine


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

        if tool.category == ToolCategory.TRANSFER_CALL.value:
            return self._create_transfer_call_handler(tool, function_name)

        return self._create_http_tool_handler(tool, function_name)

    def _create_http_tool_handler(self, tool: Any, function_name: str):
        """Create a handler function for an HTTP API tool.

        Args:
            tool: The ToolModel instance
            function_name: The function name used by the LLM

        Returns:
            Async handler function for the HTTP API tool
        """

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

                await function_call_params.result_callback(result)

            except Exception as e:
                logger.error(f"HTTP tool '{function_name}' execution failed: {e}")
                await function_call_params.result_callback(
                    {"status": "error", "error": str(e)}
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
                    await self._engine.end_call_with_reason(
                        EndTaskReason.END_CALL_TOOL_REASON.value,
                        abort_immediately=False,
                    )
                else:
                    # No message - end call immediately
                    logger.info("Ending call immediately (no goodbye message)")
                    await self._engine.end_call_with_reason(
                        EndTaskReason.END_CALL_TOOL_REASON.value, abort_immediately=True
                    )

            except Exception as e:
                logger.error(f"End call tool '{function_name}' execution failed: {e}")
                # Still try to end the call even if there's an error
                await self._engine.end_call_with_reason(
                    EndTaskReason.UNEXPECTED_ERROR.value, abort_immediately=True
                )

        return end_call_handler

    def _create_transfer_call_handler(self, tool: Any, function_name: str):
        """Create a handler function for a transfer call tool.

        Args:
            tool: The ToolModel instance
            function_name: The function name used by the LLM

        Returns:
            Async handler function for the transfer call tool
        """

        async def play_hold_music_loop(stop_event: asyncio.Event) -> None:
            """Play hold music in a loop until stop_event is set."""
            sample_rate = self._engine._audio_out_sample_rate
            try:
                hold_audio = load_hold_audio(sample_rate)
                duration_ms = get_hold_audio_duration_ms(sample_rate)
                duration_secs = duration_ms / 1000.0

                logger.info(
                    f"Starting hold music loop at {sample_rate}Hz, "
                    f"duration={duration_secs:.2f}s per loop"
                )

                while not stop_event.is_set():
                    # Queue the hold audio frame
                    frame = OutputAudioRawFrame(
                        audio=hold_audio,
                        sample_rate=sample_rate,
                        num_channels=1,
                    )
                    await self._engine.task.queue_frame(frame)

                    # Wait for the audio to play or until stopped
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=duration_secs)
                        break  # Stop event was set
                    except asyncio.TimeoutError:
                        pass  # Continue looping

                logger.info("Hold music loop stopped")

            except Exception as e:
                logger.error(f"Error playing hold music: {e}")

        async def transfer_call_handler(
            function_call_params: FunctionCallParams,
        ) -> None:
            logger.info(f"Transfer Call Tool EXECUTED: {function_name}")

            stop_hold_music = asyncio.Event()
            hold_music_task: Optional[asyncio.Task] = None

            try:
                # Get the transfer call configuration
                config = tool.definition.get("config", {})
                transfer_number = config.get("transferNumber", "")
                transfer_message = config.get("transferMessage", "")

                if not transfer_number:
                    logger.error("Transfer number not configured")
                    await function_call_params.result_callback(
                        {"status": "error", "error": "Transfer number not configured"}
                    )
                    return

                logger.info(f"Initiating transfer to: {transfer_number}")

                # Mute pipeline before playing transfer message
                self._engine.mute_pipeline()

                # Play transfer message if configured
                if transfer_message:
                    logger.info(f"Playing transfer message: {transfer_message}")
                    await self._engine.task.queue_frame(TTSSpeakFrame(transfer_message))

                # Store transfer intent in gathered context
                self._engine._gathered_context["transfer_requested"] = True
                self._engine._gathered_context["transfer_number"] = transfer_number

                # Start playing hold music in the background
                hold_music_task = asyncio.create_task(
                    play_hold_music_loop(stop_hold_music)
                )

                # Wait for external signal to proceed with transfer (30s timeout)
                workflow_run_id = self._engine._workflow_run_id
                logger.info(
                    f"Waiting for transfer signal for workflow_run_id: {workflow_run_id}"
                )

                transfer_event = await wait_for_transfer_signal(
                    workflow_run_id=workflow_run_id,
                    timeout_seconds=8.0,
                )

                # Stop hold music
                stop_hold_music.set()

                if transfer_event is None:
                    # Timeout - transfer failed
                    logger.warning("Transfer signal timed out")
                    self._engine._gathered_context["transfer_status"] = "timed_out"
                    await function_call_params.result_callback(
                        {"status": "error", "error": "Transfer signal timed out"}
                    )
                    return

                if transfer_event.type == TransferEventType.TRANSFER_CANCEL.value:
                    # Cancelled - transfer failed
                    logger.info("Transfer was cancelled")
                    self._engine._gathered_context["transfer_status"] = "cancelled"
                    await function_call_params.result_callback(
                        {"status": "error", "error": "Transfer was cancelled"}
                    )
                    return

                # Success - proceed with transfer
                logger.info("Transfer signal received - proceeding with transfer")
                self._engine._gathered_context["transfer_status"] = "success"

                # Lets send result callback so that timeout task is cancelled. Lets not
                # run llm
                await function_call_params.result_callback(
                    {"status": "error", "error": "Transfer was cancelled"},
                    properties=FunctionCallResultProperties(run_llm=False),
                )

                # Terminate the call after the call is added to the conference
                await self._engine.end_call_with_reason(
                    EndTaskReason.CALL_TRANSFERRED.value,
                    abort_immediately=True,
                )

            except Exception as e:
                logger.error(
                    f"Transfer call tool '{function_name}' execution failed: {e}"
                )
                await function_call_params.result_callback(
                    {"status": "error", "error": str(e)}
                )
            finally:
                # Ensure hold music is stopped
                stop_hold_music.set()
                if hold_music_task and not hold_music_task.done():
                    hold_music_task.cancel()
                    try:
                        await hold_music_task
                    except asyncio.CancelledError:
                        pass

        return transfer_call_handler
