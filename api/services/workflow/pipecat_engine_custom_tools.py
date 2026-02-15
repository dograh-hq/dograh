"""Custom tool management for PipecatEngine.

This module handles fetching, registering, and executing user-defined tools
during workflow execution.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

from api.constants import APP_ROOT_DIR
from api.db import db_client
from api.enums import ToolCategory
from api.services.telephony.call_transfer_manager import get_call_transfer_manager
from api.services.telephony.factory import get_telephony_provider
from api.services.telephony.transfer_event_protocol import TransferContext
from api.services.workflow.disposition_mapper import (
    get_organization_id_from_workflow_run,
)
from api.services.workflow.pipecat_engine_utils import get_function_schema
from api.services.workflow.tools.custom_tool import (
    execute_http_tool,
    tool_to_function_schema,
)
from api.utils.hold_audio import load_hold_audio
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
                handler, disable_timeout, cancel_on_interruption = self._create_handler(
                    tool, function_name
                )
                self._engine.llm.register_function(
                    function_name,
                    handler,
                    cancel_on_interruption=cancel_on_interruption,
                    disable_timeout=disable_timeout,
                )

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
        # Whether to disable function call timeout
        disable_timeout = False
        cancel_on_interruption = True

        if tool.category == ToolCategory.END_CALL.value:
            cancel_on_interruption = False
            handler = self._create_end_call_handler(tool, function_name)
        elif tool.category == ToolCategory.TRANSFER_CALL.value:
            disable_timeout = True
            cancel_on_interruption = False
            handler = self._create_transfer_call_handler(tool, function_name)
        else:
            handler = self._create_http_tool_handler(tool, function_name)

        return handler, disable_timeout, cancel_on_interruption

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

                # Get telephony provider directly (no HTTP round-trip)
                provider = await get_telephony_provider(organization_id)
                if not provider.supports_transfers() or not provider.validate_config():
                    validation_error_result = {
                        "status": "failed",
                        "message": "I'm sorry, there's an issue with this call transfer. Please contact support.",
                        "action": "transfer_failed",
                        "reason": "provider_does_not_support_transfer",
                        "end_call": False,
                    }
                    await self._handle_transfer_result(
                        validation_error_result, function_call_params, properties
                    )
                    return

                # Get original callSID from gathered_context
                workflow_run = await db_client.get_workflow_run_by_id(
                    self._engine._workflow_run_id
                )
                original_call_sid = workflow_run.gathered_context.get("call_id")

                # Generate a unique transfer ID for tracking this transfer
                transfer_id = str(uuid.uuid4())

                # Compute conference name from original call SID
                conference_name = f"transfer-{original_call_sid}"

                # Mute the pipeline
                self._engine.set_mute_pipeline(True)

                # Initiate transfer via provider with inline TwiML
                transfer_result = await provider.transfer_call(
                    destination=destination,
                    transfer_id=transfer_id,
                    conference_name=conference_name,
                    timeout=timeout_seconds,
                )

                call_sid = transfer_result.get("call_sid")
                logger.info(f"Transfer call initiated successfully: {call_sid}")

                # TODO: Possible race here between saving the transfer context
                # and getting a callback response from Twilio? Should we store_transfer_context
                # before sending request to Twilio and update the transfer context afterwards?

                # Store transfer context in Redis
                call_transfer_manager = await get_call_transfer_manager()
                transfer_context = TransferContext(
                    transfer_id=transfer_id,
                    call_sid=call_sid,
                    target_number=destination,
                    tool_uuid=tool.tool_uuid,
                    original_call_sid=original_call_sid,
                    conference_name=conference_name,
                    initiated_at=time.time(),
                )
                await call_transfer_manager.store_transfer_context(transfer_context)

                # Wait for status callback completion using Redis pub/sub
                logger.info(
                    f"Transfer call initiated for {destination} (transfer_id={transfer_id}), waiting for completion..."
                )

                # Start hold music during transfer waiting period
                hold_music_stop_event = asyncio.Event()
                hold_music_task = None

                try:
                    # Use audio config for sample rate (set during pipeline setup)
                    sample_rate = (
                        self._engine._audio_config.transport_out_sample_rate
                        if self._engine._audio_config
                        else 8000
                    )

                    logger.info(
                        f"Starting hold music at {sample_rate}Hz while waiting for transfer"
                    )

                    # Start hold music as background task
                    hold_music_task = asyncio.create_task(
                        self.play_hold_music_loop(hold_music_stop_event, sample_rate)
                    )

                    # Wait for transfer completion using Redis pub/sub
                    logger.info("Waiting for transfer completion via Redis pub/sub...")
                    transfer_event = (
                        await call_transfer_manager.wait_for_transfer_completion(
                            transfer_id, timeout_seconds
                        )
                    )

                except Exception as e:
                    logger.error(f"Error during transfer wait: {e}")
                    transfer_event = None

                finally:
                    # Single cleanup point: stop hold music, unmute pipeline, remove context
                    logger.info(
                        "Transfer wait ended, cleaning up hold music, pipeline state, and transfer context"
                    )
                    hold_music_stop_event.set()
                    if hold_music_task:
                        await hold_music_task
                    self._engine.set_mute_pipeline(False)
                    await call_transfer_manager.remove_transfer_context(transfer_id)

                # Handle result (after cleanup)
                if transfer_event:
                    final_result = transfer_event.to_result_dict()
                    await self._handle_transfer_result(
                        final_result, function_call_params, properties
                    )
                else:
                    logger.error(
                        f"Transfer call timed out or failed after {timeout_seconds} seconds"
                    )
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
                logger.error(
                    f"Transfer call tool '{function_name}' execution failed: {e}"
                )
                self._engine.set_mute_pipeline(False)

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
            response_properties = FunctionCallResultProperties(run_llm=False)
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
            logger.info(f"Transfer failed ({reason}), informing user")

            await function_call_params.result_callback(
                {
                    "status": "transfer_failed",
                    "reason": reason,
                    "message": "Transfer failed",
                }
            )
        else:
            # Unknown action, treat as generic success
            logger.warning(f"Unknown transfer action: {action}, treating as success")
            await function_call_params.result_callback(result)

    async def play_hold_music_loop(
        self, stop_event: asyncio.Event, sample_rate: int = 8000
    ):
        """Play hold music in a loop until stop event is triggered.

        Args:
            stop_event: Event to stop the hold music loop
            sample_rate: Sample rate for the hold music (default 8000Hz for Twilio)
        """
        try:
            # Path to hold music file based on sample rate
            hold_music_file = (
                APP_ROOT_DIR / "assets" / f"transfer_hold_ring_{sample_rate}.wav"
            )
            hold_audio_data = load_hold_audio(hold_music_file, sample_rate)
            num_samples = len(hold_audio_data) // 2
            duration = int(num_samples / sample_rate)

            logger.info(f"Starting hold music loop with file: {hold_music_file}")

            while not stop_event.is_set():
                # Queue the hold audio frame
                frame = OutputAudioRawFrame(
                    audio=hold_audio_data,
                    sample_rate=sample_rate,
                    num_channels=1,
                )
                await self._engine.task.queue_frame(frame)

                # Wait for the audio to play or until stopped
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=duration + 1.5)
                    break  # Stop event was set
                except asyncio.TimeoutError:
                    pass  # Continue looping

            logger.info("Hold music loop stopped")

        except Exception as e:
            logger.error(f"Error in hold music loop: {e}")
