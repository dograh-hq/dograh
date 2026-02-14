"""Custom tool management for PipecatEngine.

This module handles fetching, registering, and executing user-defined tools
during workflow execution.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any, Optional

import aiohttp
import httpx
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
from pipecat.frames.frames import (
    FunctionCallResultProperties,
    TTSSpeakFrame,
    OutputAudioRawFrame,
)
from pipecat.services.llm_service import FunctionCallParams
from pipecat.utils.enums import EndTaskReason
from pipecat.transports.websocket.fastapi import FastAPIWebsocketClient

from api.utils.hold_audio import load_hold_audio
from api.services.telephony.call_transfer_manager import get_call_transfer_manager
from api.services.telephony.transfer_event_protocol import (
    TransferEvent,
    TransferContext,
    TransferEventType,
)

from dograh.api.utils.common import get_backend_endpoints

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
        elif tool.category == ToolCategory.TRANSFER_CALL.value:
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

        properties = FunctionCallResultProperties(run_llm=False)

        async def transfer_call_handler(
            function_call_params: FunctionCallParams,
        ) -> None:
            logger.info(f"Transfer Call Tool EXECUTED: {function_name}")
            logger.info(f"Arguments: {function_call_params.arguments}")

            try:
                # Get the transfer call configuration
                config = tool.definition.get("config", {})
                destination = config.get("destination", "")
                message_type = config.get("messageType", "none")
                custom_message = config.get("customMessage", "")
                timeout_seconds = config.get(
                    "timeout", 30
                )  # Default 30 seconds if not configured

                # Validate destination phone number
                if not destination or not destination.strip():
                    validation_error_result = {
                        "status": "failed",
                        "message": "I'm sorry, but I don't have a phone number configured for the transfer. Please contact support to set up call transfer.",
                        "action": "transfer_failed",
                        "reason": "no_destination",
                        "end_call": True,
                    }
                    await self._handle_transfer_result(
                        validation_error_result, function_call_params, properties
                    )
                    return

                # Validate E.164 format
                E164_PHONE_REGEX = r"^\+[1-9]\d{1,14}$"
                if not re.match(E164_PHONE_REGEX, destination):
                    validation_error_result = {
                        "status": "failed",
                        "message": "I'm sorry, but the transfer phone number appears to be invalid. Please contact support to verify the transfer settings.",
                        "action": "transfer_failed",
                        "reason": "invalid_destination",
                        "end_call": True,
                    }
                    await self._handle_transfer_result(
                        validation_error_result, function_call_params, properties
                    )
                    return

                if message_type == "custom" and custom_message:
                    logger.info(f"Playing pre-transfer message: {custom_message}")
                    await self._engine.task.queue_frame(TTSSpeakFrame(custom_message))

                # Get original call information from Pipecat context
                from pipecat.utils.run_context import get_current_call_sid

                original_call_sid = get_current_call_sid()
                caller_number = None  # TODO: check if this is redundant now

                logger.info(f"Found original call context: call_id={original_call_sid}")

                # Get organization ID for provider configuration
                organization_id = await self.get_organization_id()
                if not organization_id:
                    validation_error_result = {
                        "status": "failed",
                        "message": "I'm sorry, there's an issue with this call transfer. Please contact support.",
                        "action": "transfer_failed",
                        "reason": "no_organization_id",
                        "end_call": False,
                    }
                    await self._handle_transfer_result(
                        validation_error_result, function_call_params, properties
                    )
                    return
                #TODO: check if everything in transfer_data is still needed
                # Prepare transfer request data
                transfer_data = {
                    "destination": destination,
                    "organization_id": organization_id,  # Required for provider configuration
                    "tool_call_id": function_call_params.tool_call_id,  # Use LLM's tool call ID for pipeline coordination
                    "tool_uuid": tool.tool_uuid,  # Add tool UUID for tracing and validation
                    "original_call_sid": original_call_sid,  # Original caller's call SID
                    "caller_number": caller_number,  # Original caller's phone number
                }


                import time

                # Get backend endpoint URL
                backend_url, _ = await get_backend_endpoints()

                # Get transfer coordinator for Redis-based coordination
                call_transfer_manager = await get_call_transfer_manager()

                # Now initiate the transfer call
                transfer_url = f"{backend_url}/api/v1/telephony/call-transfer"

                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        transfer_url,
                        json=transfer_data,
                        headers={"Content-Type": "application/json"},
                        # Authentication headers added by provider if needed
                    )

                    if response.status_code == 200:
                        result_data = response.json()
                        logger.info(f"Transfer initiated successfully: {result_data}")

                        # Wait for webhook completion using standard Pipecat async pattern
                        logger.info(
                            f"Transfer call initiated for {destination}, waiting for webhook completion..."
                        )

                        # Start hold music during transfer waiting period
                        hold_music_stop_event = asyncio.Event()
                        hold_music_task = None

                        try:
                            # Mute the pipeline to prevent further LLM generations during transfer
                            logger.info("Muting pipeline during transfer call")
                            self._engine.set_mute_pipeline(True)

                            # Determine sample rate from transport (default to 8000Hz for Twilio)
                            sample_rate = 8000
                            if hasattr(self._engine.transport, "output") and hasattr(
                                self._engine.transport.output(), "sample_rate"
                            ):
                                sample_rate = getattr(
                                    self._engine.transport.output(), "sample_rate", 8000
                                )

                            logger.info(
                                f"Starting hold music at {sample_rate}Hz while waiting for transfer"
                            )

                            # Start hold music as background task
                            hold_music_task = asyncio.create_task(
                                self.play_hold_music_loop(
                                    hold_music_stop_event, sample_rate
                                )
                            )

                            # Wait for transfer completion using Redis pub/sub
                            logger.info(
                                "Waiting for transfer completion via Redis pub/sub..."
                            )
                            transfer_event = (
                                await call_transfer_manager.wait_for_transfer_completion(
                                    transfer_data["tool_call_id"], timeout_seconds
                                )
                            )

                            # Stop hold music and unmute pipeline
                            logger.info(
                                "Transfer completed, stopping hold music and unmuting pipeline"
                            )
                            hold_music_stop_event.set()
                            if hold_music_task:
                                await hold_music_task
                            self._engine.set_mute_pipeline(False)

                            if transfer_event:
                                # Get result from transfer event
                                final_result = transfer_event.to_result_dict()

                                # Get transfer context for caller number
                                transfer_context = (
                                    await call_transfer_manager.get_transfer_context(
                                        transfer_data["tool_call_id"]
                                    )
                                )
                                if transfer_context and transfer_context.caller_number:
                                    final_result["caller_number"] = (
                                        transfer_context.caller_number
                                    )

                                # Handle the transfer result and inform user appropriately
                                await self._handle_transfer_result(
                                    final_result, function_call_params, properties
                                )
                            else:
                                # Handle timeout case
                                logger.error(
                                    f"Transfer call timed out after {timeout_seconds} seconds"
                                )

                                # Create timeout result and handle it through the same flow
                                timeout_result = {
                                    "status": "failed",
                                    "message": "I'm sorry, but the call is taking longer than expected to connect. The person might not be available right now. Please try calling back later.",
                                    "action": "transfer_failed",
                                    "reason": "timeout",
                                    "end_call": True,
                                }
                                await self._handle_transfer_result(
                                    timeout_result, function_call_params, properties
                                )

                        except Exception as e:
                            logger.error(f"Error during transfer wait: {e}")

                            # Stop hold music and unmute pipeline on error
                            logger.info(
                                "Transfer error, stopping hold music and unmuting pipeline"
                            )
                            hold_music_stop_event.set()
                            if hold_music_task:
                                await hold_music_task
                            self._engine.set_mute_pipeline(False)

                            # Handle error case
                            error_result = {
                                "status": "failed",
                                "message": "I'm sorry, but there was an issue processing the transfer. Please try again.",
                                "action": "transfer_failed",
                                "reason": "system_error",
                                "end_call": True,
                            }
                            await self._handle_transfer_result(
                                error_result, function_call_params, properties
                            )

                    else:
                        error_data = (
                            response.json()
                            if response.content
                            else {"error": "Unknown error"}
                        )
                        logger.error(
                            f"Transfer initiation failed: {response.status_code} - {error_data}"
                        )

                        # Handle initiation failure with user-friendly message
                        initiation_failure_result = {
                            "status": "failed",
                            "message": "I'm sorry, but I'm having trouble setting up the call transfer right now. There might be a technical issue. Please try again later or contact support.",
                            "action": "transfer_failed",
                            "reason": "initiation_failed",
                            "end_call": True,
                        }

                        await self._handle_transfer_result(
                            initiation_failure_result, function_call_params, properties
                        )

            except httpx.TimeoutException:
                logger.error(f"Transfer call '{function_name}' HTTP request timed out")

                # Handle HTTP timeout with user-friendly message
                http_timeout_result = {
                    "status": "failed",
                    "message": "I'm sorry, but there seems to be a network issue preventing me from setting up the call transfer. Please try again in a moment.",
                    "action": "transfer_failed",
                    "reason": "network_timeout",
                    "end_call": True,
                }

                await self._handle_transfer_result(
                    http_timeout_result, function_call_params, properties
                )

            except Exception as e:
                logger.error(
                    f"Transfer call tool '{function_name}' execution failed: {e}"
                )

                # Handle generic exception with user-friendly message
                exception_result = {
                    "status": "failed",
                    "message": "I'm sorry, but something went wrong while trying to transfer your call. Please try again later or contact support if the problem persists.",
                    "action": "transfer_failed",
                    "reason": "execution_error",
                    "end_call": True,
                }

                await self._handle_transfer_result(
                    exception_result, function_call_params, properties
                )

        return transfer_call_handler

    async def _handle_transfer_result(
        self, result: dict, function_call_params, properties
    ):
        """Handle different transfer call outcomes and take appropriate action."""
        action = result.get("action", "")
        status = result.get("status", "")
        message = result.get("message", "")
        should_end_call = result.get("end_call", False)

        logger.info(f"Handling transfer result: action={action}, status={status}")

        if action == "transfer_success":
            # Successful transfer - add original caller to conference and end pipeline
            conference_id = result.get("conference_id")
            original_call_sid = result.get("original_call_sid")
            transfer_call_sid = result.get("transfer_call_sid")

            logger.info(
                f"Transfer successful! Conference: {conference_id}, Original: {original_call_sid}, Transfer: {transfer_call_sid}"
            )

            # Inform LLM of success and end the call with Transfer call reason
            response_properties = FunctionCallResultProperties(
                run_llm=False
            )
            await function_call_params.result_callback(
                {
                    "status": "transfer_success",
                    "message": "Transfer successful - connecting to conference",
                    "conference_id": conference_id,
                },
                properties=response_properties,
            )

            await self._engine.end_call_with_reason(
                EndTaskReason.TRANSFER_CALL.value, abort_immediately=False
            )

        elif action == "transfer_failed":
            # Transfer failed - inform user via LLM and then end the call
            reason = result.get("reason", "unknown")
            logger.info(f"Transfer failed ({reason}), informing user and ending call")

            from pipecat.frames.frames import LLMMessagesAppendFrame

            # Create system message with clear instructions for transfer failure
            failure_instruction = {
                "role": "system",
                "content": f"IMPORTANT: The transfer call has FAILED. Reason: {reason}. You must inform the customer about this failure using this message: '{message}' Then immediately say goodbye and end the conversation. Do NOT ask if they need anything else or continue the conversation. Do NOT continue with transfer language.",
            }

            # Push the system message to LLM context
            await self._engine.task.queue_frame(
                LLMMessagesAppendFrame([failure_instruction], run_llm=True)
            )

            # Also send the function call result for consistency
            response_properties = FunctionCallResultProperties(
                run_llm=False
            )  # LLM will be triggered by system message
            await function_call_params.result_callback(
                {"status": "transfer_failed", "reason": reason, "message": message},
                properties=response_properties,
            )

            # Set appropriate disposition for analytics
            disposition_map = {
                "no_answer": "transfer_no_answer",
                "busy": "transfer_busy",
                "call_failed": "transfer_failed",
                "timeout": "transfer_timeout",
                "no_destination": "transfer_config_error",
                "invalid_destination": "transfer_config_error",
                "initiation_failed": "transfer_system_error",
                "network_timeout": "transfer_system_error",
                "execution_error": "transfer_system_error",
            }

            disposition = disposition_map.get(reason, "transfer_failed")
            logger.info(
                f"Setting disposition: {disposition} for transfer failure reason: {reason}"
            )

            # Give the LLM time to speak the message, then end the call with disposition
            # We'll schedule the end call after a brief delay to allow TTS
            logger.info("Scheduling call end after LLM delivers failure message")

            import asyncio

            # Schedule call end after 3 seconds to allow LLM to speak
            async def delayed_end_call():
                import asyncio
                await asyncio.sleep(3)
                await self._engine.end_call_with_reason(
                    f"transfer_failed_{reason}",  # Include specific reason in end reason
                    abort_immediately=False,  # Allow any queued speech to complete
                )

            # Create task to end call asynchronously
            asyncio.create_task(delayed_end_call())

        elif action == "transfer_completed":
            # This should no longer happen since we ignore "completed" status in webhook
            # to avoid overriding successful transfers
            logger.warning(
                "Received unexpected 'transfer_completed' action - this should be ignored by webhook now"
            )
            logger.warning(
                "If you see this message, there might be an issue with the webhook status filtering"
            )

            # For safety, treat it as a generic result without ending the call
            await function_call_params.result_callback(result, properties=properties)

        else:
            # Unknown action, treat as generic success
            logger.warning(f"Unknown transfer action: {action}, treating as success")
            await function_call_params.result_callback(result, properties=properties)

    async def play_hold_music_loop(
        self, stop_event: asyncio.Event, sample_rate: int = 8000
    ):
        """Play hold music in a loop until stop event is triggered.

        Args:
            stop_event: Event to stop the hold music loop
            sample_rate: Sample rate for the hold music (default 8000Hz for Twilio)
        """
        try:
            import os

            # Path to hold music file based on sample rate
            assets_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "assets"
            )

            # Select appropriate hold music file
            if sample_rate == 16000:
                hold_music_file = os.path.join(
                    assets_dir, "transfer_hold_ring_16000.wav"
                )
            else:  # Default to 8000Hz for Twilio
                hold_music_file = os.path.join(
                    assets_dir, "transfer_hold_ring_8000.wav"
                )

            logger.info(f"Starting hold music loop with file: {hold_music_file}")

            # Load hold music audio data
            hold_audio_data = load_hold_audio(hold_music_file, sample_rate)
            if not hold_audio_data:
                logger.error("Failed to load hold music data")
                return

            # Convert bytes to audio frames - each frame should be about 20ms worth of audio
            # For 8000Hz: 20ms = 160 samples = 320 bytes (16-bit)
            # For 16000Hz: 20ms = 320 samples = 640 bytes (16-bit)
            frame_size = 320 if sample_rate == 8000 else 640

            audio_data = hold_audio_data
            total_length = len(audio_data)
            position = 0

            logger.info(
                f"Hold music loaded: {total_length} bytes, frame size: {frame_size}"
            )

            while not stop_event.is_set():
                # Extract audio chunk
                if position + frame_size > total_length:
                    # Reached end of audio, loop back to beginning
                    position = 0

                audio_chunk = audio_data[position : position + frame_size]
                position += frame_size

                # Create audio frame
                audio_frame = OutputAudioRawFrame(
                    audio=audio_chunk,
                    sample_rate=sample_rate,
                    num_channels=1,
                )

                # Queue the frame
                await self._engine.task.queue_frame(audio_frame)

                # Sleep for frame duration (20ms)
                await asyncio.sleep(0.02)

            logger.info("Hold music loop stopped")

        except Exception as e:
            logger.error(f"Error in hold music loop: {e}")
