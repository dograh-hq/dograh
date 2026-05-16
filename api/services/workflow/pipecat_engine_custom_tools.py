"""Custom tool management for PipecatEngine.

This module handles fetching, registering, and executing user-defined tools
during workflow execution.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import (
    FunctionCallResultProperties,
    TTSSpeakFrame,
)
from pipecat.services.llm_service import FunctionCallParams
from pipecat.utils.enums import EndTaskReason

from api.db import db_client
from api.enums import ToolCategory, WorkflowRunMode
from api.services.pipecat.audio_playback import play_audio, play_audio_loop
from api.services.telephony.call_transfer_manager import get_call_transfer_manager
from api.services.telephony.factory import get_telephony_provider_for_run
from api.services.telephony.transfer_event_protocol import TransferContext
from api.services.workflow.tools.calculator import get_calculator_tools, safe_calculator
from api.services.workflow.tools.custom_tool import (
    execute_http_tool,
    tool_to_function_schema,
)

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine import PipecatEngine


def get_function_schema(
    function_name: str,
    description: str,
    *,
    properties: Dict[str, Any] | None = None,
    required: List[str] | None = None,
) -> FunctionSchema:
    """Create a FunctionSchema definition that can later be transformed into
    the provider-specific format (OpenAI, Gemini, etc.).

    The helper keeps the public signature backward-compatible – callers that
    only pass ``function_name`` and ``description`` continue to work and will
    define a parameter-less function.
    """

    return FunctionSchema(
        name=function_name,
        description=description,
        properties=properties or {},
        required=required or [],
    )


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

    async def _play_config_message(
        self, config: dict, *, append_to_context: bool = False
    ) -> bool:
        """Play a message from tool config — text or pre-recorded audio.

        Returns True if a message was queued, False otherwise.
        """
        message_type = config.get("messageType", "none")

        if message_type == "audio":
            recording_pk = config.get("audioRecordingId")
            if recording_pk and self._engine._fetch_recording_audio:
                result = await self._engine._fetch_recording_audio(
                    recording_pk=int(recording_pk)
                )
                if result:
                    await play_audio(
                        result.audio,
                        sample_rate=self._engine._audio_config.pipeline_sample_rate
                        if self._engine._audio_config
                        else 16000,
                        queue_frame=self._engine._transport_output.queue_frame,
                        transcript=result.transcript,
                        persist_to_logs=True,
                    )
                    return True
                else:
                    logger.warning(f"Failed to fetch recording pk={recording_pk}")
            return False

        if message_type == "custom":
            custom_message = config.get("customMessage", "")
            if custom_message:
                await self._engine.task.queue_frame(
                    TTSSpeakFrame(
                        custom_message,
                        append_to_context=append_to_context,
                        persist_to_logs=True,
                    )
                )
                return True

        return False

    async def get_organization_id(self) -> Optional[int]:
        """Get the organization ID from the engine (shared cache)."""
        return await self._engine._get_organization_id()

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
                if tool.category == ToolCategory.CALCULATOR.value:
                    # Built-in calculator: return pre-defined schemas
                    for tool_def in get_calculator_tools():
                        func = tool_def["function"]
                        schemas.append(
                            get_function_schema(
                                func["name"],
                                func["description"],
                                properties=func["parameters"]["properties"],
                                required=func["parameters"]["required"],
                            )
                        )
                    continue

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
                if tool.category == ToolCategory.CALCULATOR.value:
                    self._register_calculator_handler()
                    logger.debug(
                        f"Registered calculator tool handler "
                        f"(tool_uuid: {tool.tool_uuid})"
                    )
                    continue

                schema = tool_to_function_schema(tool)
                function_name = schema["function"]["name"]

                # Create and register the handler
                handler, timeout_secs = self._create_handler(tool, function_name)
                self._engine.llm.register_function(
                    function_name,
                    handler,
                    timeout_secs=timeout_secs,
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
        timeout_secs: Optional[float] = None

        if tool.category == ToolCategory.END_CALL.value:
            handler = self._create_end_call_handler(tool, function_name)
        elif tool.category == ToolCategory.TRANSFER_CALL.value:
            timeout_secs = 120.0
            handler = self._create_transfer_call_handler(tool, function_name)
        else:
            handler = self._create_http_tool_handler(tool, function_name)

        return handler, timeout_secs

    def _register_calculator_handler(self) -> None:
        """Register the built-in calculator function with the LLM."""

        async def calculate_func(function_call_params: FunctionCallParams) -> None:
            logger.info("LLM Function Call EXECUTED: safe_calculator")
            logger.info(f"Arguments: {function_call_params.arguments}")
            try:
                expr = function_call_params.arguments.get("expression", "")
                result = safe_calculator(expr)
                await function_call_params.result_callback(
                    {"expression": expr, "result": result}
                )
            except Exception as e:
                await function_call_params.result_callback({"error": str(e)})

        self._engine.llm.register_function("safe_calculator", calculate_func)

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
                # Queue custom message before executing the API call
                # Queue custom message (text or audio) before executing the API call
                config = tool.definition.get("config", {}) if tool.definition else {}
                custom_msg_type = config.get("customMessageType", "text")
                custom_message = config.get("customMessage", "")
                if custom_msg_type == "audio":
                    recording_pk = config.get("customMessageRecordingId")
                    if recording_pk and self._engine._fetch_recording_audio:
                        logger.info(
                            f"Playing audio message before HTTP tool: pk={recording_pk}"
                        )
                        self._engine._queued_speech_mute_state = "waiting"
                        result = await self._engine._fetch_recording_audio(
                            recording_pk=int(recording_pk)
                        )
                        if result:
                            await play_audio(
                                result.audio,
                                sample_rate=self._engine._audio_config.pipeline_sample_rate
                                if self._engine._audio_config
                                else 16000,
                                queue_frame=self._engine._transport_output.queue_frame,
                                transcript=result.transcript,
                                persist_to_logs=True,
                            )
                elif custom_message:
                    logger.info(
                        f"Playing custom message before HTTP tool: {custom_message}"
                    )
                    self._engine._queued_speech_mute_state = "waiting"
                    await self._engine.task.queue_frame(
                        TTSSpeakFrame(
                            custom_message,
                            append_to_context=False,
                            persist_to_logs=True,
                        )
                    )

                result = await execute_http_tool(
                    tool=tool,
                    arguments=function_call_params.arguments,
                    call_context_vars=self._engine._call_context_vars,
                    gathered_context_vars=self._engine._gathered_context,
                    organization_id=await self.get_organization_id(),
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

                # Handle end call reason if enabled
                end_call_reason_enabled = config.get("endCallReason", False)
                if end_call_reason_enabled:
                    reason = (
                        function_call_params.arguments.get("reason", "")
                        or "end_call_tool"
                    )
                    logger.info(f"End call reason: {reason}")
                    self._engine._gathered_context["call_disposition"] = reason
                    call_tags = self._engine._gathered_context.get("call_tags", [])
                    if "end_call_tool" not in call_tags:
                        call_tags.append("end_call_tool")
                    self._engine._gathered_context["call_tags"] = call_tags

                # Send result callback first
                await function_call_params.result_callback(
                    {"status": "success", "action": "ending_call"},
                    properties=properties,
                )

                played = await self._play_config_message(config)
                if played:
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
                timeout_seconds = config.get(
                    "timeout", 30
                )  # Default 30 seconds if not configured

                # Check if this is a WebRTC call - transfers are not supported
                workflow_run = await db_client.get_workflow_run_by_id(
                    self._engine._workflow_run_id
                )
                if workflow_run.mode in [
                    WorkflowRunMode.WEBRTC.value,
                    WorkflowRunMode.SMALLWEBRTC.value,
                ]:
                    webrtc_error_result = {
                        "status": "failed",
                        "message": "I'm sorry, but call transfers are not available for web calls. Please try a telephony call.",
                        "action": "transfer_failed",
                        "reason": "webrtc_not_supported",
                    }
                    await self._handle_transfer_result(
                        webrtc_error_result, function_call_params, properties
                    )
                    return

                # Validate destination phone number
                if not destination or not destination.strip():
                    validation_error_result = {
                        "status": "failed",
                        "message": "I'm sorry, but I don't have a phone number configured for the transfer. Please contact support to set up call transfer.",
                        "action": "transfer_failed",
                        "reason": "no_destination",
                    }
                    await self._handle_transfer_result(
                        validation_error_result, function_call_params, properties
                    )
                    return

                # Validate destination format based on workflow run mode
                if workflow_run.mode == WorkflowRunMode.ARI.value:
                    # For ARI provider, also accept SIP endpoints
                    SIP_ENDPOINT_REGEX = r"^(PJSIP|SIP)\/[\w\-\.@]+$"
                    E164_PHONE_REGEX = r"^\+[1-9]\d{1,14}$"

                    is_valid_sip = re.match(SIP_ENDPOINT_REGEX, destination)
                    is_valid_e164 = re.match(E164_PHONE_REGEX, destination)

                    if not (is_valid_sip or is_valid_e164):
                        validation_error_result = {
                            "status": "failed",
                            "message": "I'm sorry, but the transfer destination appears to be invalid. Please contact support to verify the transfer settings.",
                            "action": "transfer_failed",
                            "reason": "invalid_destination",
                        }
                        await self._handle_transfer_result(
                            validation_error_result, function_call_params, properties
                        )
                        return
                else:
                    # For non-ARI providers (Twilio, etc), use E.164 validation
                    E164_PHONE_REGEX = r"^\+[1-9]\d{1,14}$"
                    if not re.match(E164_PHONE_REGEX, destination):
                        validation_error_result = {
                            "status": "failed",
                            "message": "I'm sorry, but the transfer phone number appears to be invalid. Please contact support to verify the transfer settings.",
                            "action": "transfer_failed",
                            "reason": "invalid_destination",
                        }
                        await self._handle_transfer_result(
                            validation_error_result, function_call_params, properties
                        )
                        return

                played = await self._play_config_message(config)
                if played:
                    self._engine._queued_speech_mute_state = "waiting"

                # Get organization ID for provider configuration
                organization_id = await self.get_organization_id()
                if not organization_id:
                    validation_error_result = {
                        "status": "failed",
                        "message": "I'm sorry, there's an issue with this call transfer. Please contact support.",
                        "action": "transfer_failed",
                        "reason": "no_organization_id",
                    }
                    await self._handle_transfer_result(
                        validation_error_result, function_call_params, properties
                    )
                    return

                provider = await get_telephony_provider_for_run(
                    workflow_run, organization_id
                )
                if not provider.supports_transfers() or not provider.validate_config():
                    validation_error_result = {
                        "status": "failed",
                        "message": "I'm sorry, there's an issue with this call transfer. Please contact support.",
                        "action": "transfer_failed",
                        "reason": "provider_does_not_support_transfer",
                    }
                    await self._handle_transfer_result(
                        validation_error_result, function_call_params, properties
                    )
                    return

                original_call_sid = workflow_run.gathered_context.get("call_id")

                # Generate a unique transfer ID for tracking this transfer
                transfer_id = str(uuid.uuid4())

                # Compute conference name from original call SID
                conference_name = f"transfer-{original_call_sid}"

                # Store initial transfer context in Redis before provider call to avoid race condition
                call_transfer_manager = await get_call_transfer_manager()
                transfer_context = TransferContext(
                    transfer_id=transfer_id,
                    call_sid=None,  # Will be updated after provider response
                    target_number=destination,
                    tool_uuid=tool.tool_uuid,
                    original_call_sid=original_call_sid,
                    conference_name=conference_name,
                    initiated_at=time.time(),
                    workflow_run_id=self._engine._workflow_run_id,
                )
                await call_transfer_manager.store_transfer_context(transfer_context)

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

                # Update transfer context with actual call_sid from provider response
                transfer_context.call_sid = call_sid
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
                        play_audio_loop(
                            stop_event=hold_music_stop_event,
                            sample_rate=sample_rate,
                            queue_frame=self._engine._transport_output.queue_frame,
                        )
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
                    # Cleanup hold music and pipeline state
                    # Transfer context cleanup is handled by respective transfer call strategies
                    logger.info(
                        "Transfer wait ended, cleaning up hold music and pipeline state"
                    )
                    hold_music_stop_event.set()
                    if hold_music_task:
                        await hold_music_task
                    self._engine.set_mute_pipeline(False)

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
                }

                await self._handle_transfer_result(
                    exception_result, function_call_params, properties
                )

        return transfer_call_handler

    async def _handle_transfer_result(
        self, result: dict, function_call_params, properties
    ):
        """Handle transfer call outcomes from any telephony provider (Twilio, ARI, etc).

        This method is provider-agnostic and processes standardized result dictionaries
        from transfer completion events, validation failures, timeouts, and errors.

        Args:
            result: Standardized result dict with keys: action, status, reason, message
            function_call_params: LLM function call parameters for response callback
            properties: Function call result properties (e.g., run_llm setting)
        """
        action = result.get("action", "")
        status = result.get("status", "")

        logger.info(f"Handling transfer result: action={action}, status={status}")

        if action == "destination_answered":
            # Transfer destination answered - proceeding with bridge swap/conference join
            conference_id = result.get("conference_id")
            original_call_sid = result.get("original_call_sid")
            transfer_call_sid = result.get("transfer_call_sid")

            logger.info(
                f"Transfer destination answered! Conference/Bridge: {conference_id}, "
                f"Original: {original_call_sid}, Transfer: {transfer_call_sid}"
            )

            # Inform LLM of success and end the call (no further LLM processing needed)
            response_properties = FunctionCallResultProperties(run_llm=False)
            await function_call_params.result_callback(
                {
                    "status": "transfer_success",
                    "message": "Transfer destination answered - connecting calls",
                    "conference_id": conference_id,
                },
                properties=response_properties,
            )

            # End pipeline - providers complete bridge swap/conference join as final transfer leg
            await self._engine.end_call_with_reason(
                EndTaskReason.TRANSFER_CALL.value, abort_immediately=False
            )

        elif action == "transfer_failed":
            # Transfer failed - let LLM inform user with error details
            reason = result.get("reason", "unknown")
            logger.info(f"Transfer failed ({reason}), informing user via LLM")

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
