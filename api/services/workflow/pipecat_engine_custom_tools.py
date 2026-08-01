"""Custom tool management for PipecatEngine.

This module handles fetching, registering, and executing user-defined tools
during workflow execution.
"""

from __future__ import annotations

import asyncio
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
from api.services.telephony.external_pbx import resolve_external_pbx_field_mappings
from api.services.telephony.factory import get_telephony_provider_for_run
from api.services.telephony.transfer_event_protocol import TransferContext
from api.services.workflow.tools.calculator import get_calculator_tools, safe_calculator
from api.services.workflow.tools.wait import get_wait_tools
from api.services.workflow.tools.custom_tool import (
    execute_http_tool,
    tool_to_function_schema,
)
from api.services.workflow.tools.transfer_resolver import (
    TransferResolutionError,
    resolve_transfer_config,
)
from api.utils.template_renderer import render_template

if TYPE_CHECKING:
    from api.services.workflow.mcp_tool_session import McpToolSession
    from api.services.workflow.pipecat_engine import PipecatEngine


def _render_transfer_destination(
    destination_template: Any,
    call_context_vars: Optional[Dict[str, Any]],
    gathered_context_vars: Optional[Dict[str, Any]],
) -> str:
    """Resolve a transfer destination template into a concrete provider target."""

    initial_context = dict(call_context_vars or {})
    render_context: Dict[str, Any] = {
        **initial_context,
        "initial_context": initial_context,
        "gathered_context": dict(gathered_context_vars or {}),
    }
    rendered = render_template(destination_template, render_context)
    if rendered is None:
        return ""
    return str(rendered).strip()


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

    async def get_tool_schemas(
        self,
        tool_uuids: list[str],
        mcp_tool_filters: Optional[dict[str, list[str]]] = None,
    ) -> list[FunctionSchema]:
        """Fetch custom tools and convert them to function schemas.

        Args:
            tool_uuids: List of tool UUIDs to fetch
            mcp_tool_filters: Optional per-node filter mapping tool_uuid → list of
                raw MCP tool names to expose. None (default) exposes all tools.
                Empty dict or entry with [] suppresses all tools for that uuid.

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
                if tool.category == "wait":
                    self._register_wait_handler()
                    logger.debug(f"Registered wait tool handler (tool_uuid: {tool.tool_uuid})")
                    for tool_def in get_wait_tools():
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

                if tool.category == ToolCategory.MCP.value:
                    session = self._engine._mcp_sessions.get(tool.tool_uuid)
                    if session is None or not session.available:
                        logger.warning(
                            f"MCP tool '{tool.name}' ({tool.tool_uuid}) "
                            f"unavailable; skipping"
                        )
                        continue
                    allowed = (
                        None
                        if mcp_tool_filters is None
                        else set(mcp_tool_filters.get(tool.tool_uuid, []))
                    )
                    schemas.extend(session.function_schemas(allowed))
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

    async def register_handlers(
        self,
        tool_uuids: list[str],
        mcp_tool_filters: Optional[dict[str, list[str]]] = None,
    ) -> None:
        """Register custom tool execution handlers with the LLM.

        Args:
            tool_uuids: List of tool UUIDs to register handlers for
            mcp_tool_filters: Optional per-node filter mapping tool_uuid → list of
                raw MCP tool names to expose. None (default) exposes all tools.
                Empty dict or entry with [] suppresses all tools for that uuid.
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
                if tool.category == "wait":
                    self._register_wait_handler()
                    logger.debug(f"Registered wait tool handler (tool_uuid: {tool.tool_uuid})")
                    continue

                if tool.category == ToolCategory.CALCULATOR.value:
                    self._register_calculator_handler()
                    logger.debug(
                        f"Registered calculator tool handler "
                        f"(tool_uuid: {tool.tool_uuid})"
                    )
                    continue

                if tool.category == ToolCategory.MCP.value:
                    session = self._engine._mcp_sessions.get(tool.tool_uuid)
                    if session is None or not session.available:
                        logger.warning(
                            f"MCP tool '{tool.name}' ({tool.tool_uuid}) "
                            f"unavailable; skipping handler registration"
                        )
                        continue
                    allowed = (
                        None
                        if mcp_tool_filters is None
                        else set(mcp_tool_filters.get(tool.tool_uuid, []))
                    )
                    mcp_schemas = session.function_schemas(allowed)
                    for fs in mcp_schemas:
                        self._engine.llm.register_function(
                            fs.name,
                            self._create_mcp_handler(session, fs.name),
                            timeout_secs=session.call_timeout_secs,
                        )
                    logger.debug(
                        f"Registered {len(mcp_schemas)} MCP "
                        f"handlers for tool '{tool.name}' ({tool.tool_uuid})"
                    )
                    continue

                schema = tool_to_function_schema(tool)
                function_name = schema["function"]["name"]

                # Create and register the handler
                handler, timeout_secs = self._create_handler(tool, function_name)
                # End-call and transfer-call tools are workflow-control
                # boundaries even though they do not necessarily select another
                # graph node. Give them the same ordering guarantees as an
                # explicit node-transition function.
                is_node_transition = tool.category in {
                    ToolCategory.END_CALL.value,
                    ToolCategory.TRANSFER_CALL.value,
                }
                self._engine.llm.register_function(
                    function_name,
                    handler,
                    timeout_secs=timeout_secs,
                    is_node_transition=is_node_transition,
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
            timeout_secs = self._transfer_handler_timeout_secs(tool)
            handler = self._create_transfer_call_handler(tool, function_name)
        else:
            timeout_ms = ((tool.definition or {}).get("config", {}) or {}).get(
                "timeout_ms", 5000
            )
            timeout_secs = float(timeout_ms) / 1000
            handler = self._create_http_tool_handler(tool, function_name)

        return handler, timeout_secs

    def _transfer_handler_timeout_secs(self, tool: Any) -> float:
        config = (tool.definition or {}).get("config", {}) or {}
        try:
            transfer_timeout = int(config.get("timeout", 30))
        except (TypeError, ValueError):
            transfer_timeout = 30
        transfer_timeout = min(max(transfer_timeout, 5), 120)

        resolver_timeout = 0.0
        resolver = config.get("resolver")
        if config.get("destination_source", "static") == "dynamic" and isinstance(
            resolver, dict
        ):
            try:
                resolver_timeout = float(resolver.get("timeout_ms", 3000)) / 1000.0
            except (TypeError, ValueError):
                resolver_timeout = 3.0
            resolver_timeout = min(max(resolver_timeout, 0.5), 5.0)

        return float(transfer_timeout) + resolver_timeout + 15.0

    def _register_wait_handler(self) -> None:
        """Register the built-in wait function with the LLM."""

        async def wait_func(function_call_params: FunctionCallParams) -> None:
            logger.info("LLM Function Call EXECUTED: wait_for_user")
            logger.info(f"Arguments: {function_call_params.arguments}")
            
            try:
                seconds_arg = function_call_params.arguments.get("seconds", 60)
                try:
                    seconds = int(seconds_arg)
                    if seconds < 0:
                        raise ValueError("Seconds cannot be negative.")
                except (ValueError, TypeError):
                    await function_call_params.result_callback({"error": f"Invalid 'seconds' parameter: {seconds_arg}. Must be a positive integer."})
                    return
                
                # Use a hard cap of 300 seconds for the built-in wait tool
                max_wait = 300
                seconds = min(max(seconds, 1), max_wait)
                
                message = function_call_params.arguments.get("message")
                if message:
                    from pipecat.frames.frames import TTSSpeakFrame, TTSStartedFrame, TTSStoppedFrame
                    from pipecat.observers.base_observer import BaseObserver as _BaseObserver, FramePushed as _FramePushed

                    logger.info(f"Playing wait acknowledgment message: {message}")

                    # Register a temporary observer BEFORE queuing the TTS so we
                    # don't miss TTSStartedFrame if the pipeline is very fast.
                    # We require TTSStartedFrame before accepting TTSStoppedFrame to
                    # guard against stale TTSStoppedFrames from the previous response.
                    tts_done_event = asyncio.Event()

                    class _TTSDoneObserver(_BaseObserver):
                        def __init__(self):
                            super().__init__()
                            self._seen_started = False

                        async def on_push_frame(self, data: _FramePushed):
                            if isinstance(data.frame, TTSStartedFrame):
                                self._seen_started = True
                            elif isinstance(data.frame, TTSStoppedFrame) and self._seen_started:
                                logger.info("Wait tool: acknowledgment TTS finished (TTSStoppedFrame received).")
                                tts_done_event.set()

                    tts_done_observer = _TTSDoneObserver()
                    _task = self._engine.task
                    if _task:
                        _task.add_observer(tts_done_observer)

                    await self._engine.task.queue_frame(
                        TTSSpeakFrame(
                            message,
                            append_to_context=False,
                            persist_to_logs=True,
                        )
                    )

                    # Wait for TTS to actually finish, with a generous fallback timeout.
                    try:
                        await asyncio.wait_for(tts_done_event.wait(), timeout=15.0)
                    except asyncio.TimeoutError:
                        logger.warning("Wait tool: TTS done event timed out after 15s ÔÇö proceeding anyway.")

                    if _task:
                        try:
                            await _task.remove_observer(tts_done_observer)
                        except Exception as e:
                            logger.warning(f"Could not remove TTS done observer: {e}")

                logger.info(f"Pausing for {seconds} seconds...")
                
                stop_event = asyncio.Event()
                hold_music_task = None
                
                if seconds >= 5 and self._engine._audio_config and self._engine._transport_output:
                    from api.constants import APP_ROOT_DIR
                    sample_rate = self._engine._audio_config.pipeline_sample_rate
                    audio_file = str(APP_ROOT_DIR / "assets" / f"wait_music_{sample_rate}.wav")

                    hold_music_task = asyncio.create_task(
                        play_audio_loop(
                            stop_event=stop_event,
                            sample_rate=sample_rate,
                            queue_frame=self._engine._transport_output.queue_frame,
                            audio_file=audio_file,
                        )
                    )
                
                user_speech_event = asyncio.Event()
                # Issue 2 fix: capture the actual transcript text so we can include it
                # in the tool result. Without this, the TranscriptionFrame is muted by
                # FunctionCallUserMuteStrategy and the LLM never sees what the user said,
                # forcing the user to repeat themselves.
                import time
                user_speech_text: list[str] = []  # mutable container for closure capture

                # We use a pipeline observer (not the mute-filtered aggregator)
                # because FunctionCallUserMuteStrategy suppresses all
                # TranscriptionFrames and UserStartedSpeakingFrames while a
                # tool is executing. Pipeline observers receive frames *before*
                # the mute strategy suppresses them.
                from pipecat.observers.base_observer import BaseObserver, FramePushed
                from pipecat.frames.frames import TranscriptionFrame

                class _WaitInterruptObserver(BaseObserver):
                    async def on_push_frame(self, data: FramePushed):
                        if isinstance(data.frame, TranscriptionFrame) and data.frame.text.strip():
                            text = data.frame.text.strip()
                            logger.info(f"Wait tool observer: transcription received: '{text}', signalling interrupt.")
                            # Capture the text (Issue 2 fix: include in tool result)
                            if not user_speech_text:
                                user_speech_text.append(text)
                            user_speech_event.set()

                wait_observer = _WaitInterruptObserver()
                task = self._engine.task
                if task:
                    task.add_observer(wait_observer)

                elapsed = 0.0
                try:
                    # Fix #1: Never wait less than 15 seconds to avoid overlapping TTS
                    # acknowledgment and immediate "are you still there" follow-up.
                    effective_seconds = min(max(float(seconds), 15.0), float(max_wait))
                    
                    while round(elapsed, 1) < effective_seconds:
                        if self._engine.is_call_disposed():
                            break

                        if user_speech_event.is_set():
                            logger.info("Wait interrupted by user speech.")
                            break

                        await asyncio.sleep(0.1)
                        elapsed += 0.1
                finally:
                    stop_event.set()
                    if hold_music_task:
                        hold_music_task.cancel()
                        try:
                            await hold_music_task
                        except asyncio.CancelledError:
                            pass

                    if task:
                        try:
                            await task.remove_observer(wait_observer)
                        except Exception as e:
                            logger.warning(f"Could not remove wait observer: {e}")
                
                logger.info(f"Wait completed after {elapsed} seconds (requested {seconds}).")
                
                if function_call_params and function_call_params.result_callback:
                    clean_elapsed = round(elapsed, 1)
                    msg = f"Wait finished after {clean_elapsed} seconds. "
                    if user_speech_event.is_set():
                        # Issue 2 fix: The TranscriptionFrame was muted by
                        # FunctionCallUserMuteStrategy so the LLM context never received
                        # it. We inject the captured text directly into the tool result
                        # so the LLM can respond to the user's exact words.
                        captured = user_speech_text[0] if user_speech_text else None
                        if captured:
                            msg += f'The user has returned and said: "{captured}". Respond directly to what they said. Do NOT thank them for waiting, because YOU were the one waiting for THEM.'
                        else:
                            msg += "The user has returned and spoken. Please respond naturally to what they just said. Do NOT thank them for waiting, because YOU were the one waiting for THEM."
                    else:
                        msg += "The time is up. Please ask the user if they are still there and if they need further assistance. Do NOT thank them for waiting, because YOU were the one waiting for THEM."
                        
                    await function_call_params.result_callback(
                        {"status": "success", "message": msg}
                    )
            except Exception as e:
                logger.error(f"Wait tool error: {e}")
                await function_call_params.result_callback({"error": str(e)})

        # Register with a large timeout to prevent the LLM caller from timing out the wait.
        self._engine.llm.register_function("wait_for_user", wait_func, timeout_secs=330.0)

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

    def _create_mcp_handler(self, session: "McpToolSession", function_name: str):
        """Create a handler that proxies an LLM function call to a live MCP
        session. Errors are returned to the LLM as structured text so the
        agent can recover verbally; the call is never crashed."""

        async def mcp_tool_handler(
            function_call_params: FunctionCallParams,
        ) -> None:
            logger.info(f"MCP Tool EXECUTED: {function_name}")
            logger.info(f"Arguments: {function_call_params.arguments}")
            try:
                result = await session.call(
                    function_name, function_call_params.arguments or {}
                )
                await function_call_params.result_callback(result)
            except Exception as e:
                logger.error(f"MCP tool '{function_name}' failed: {e}")
                await function_call_params.result_callback(
                    {"status": "error", "error": str(e)}
                )

        return mcp_tool_handler

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
            logger.info(
                "Transfer call arguments received "
                f"argument_keys={list((function_call_params.arguments or {}).keys())}"
            )

            try:
                # Get the transfer call configuration
                config = tool.definition.get("config", {})
                destination = config.get("destination", "")
                timeout_seconds = config.get("timeout", 30)

                # Check if this is a WebRTC call - transfers are not supported
                workflow_run = await db_client.get_workflow_run_by_id(
                    self._engine._workflow_run_id
                )
                if workflow_run.mode == WorkflowRunMode.TEXTCHAT.value:
                    textchat_error_result = {
                        "status": "failed",
                        "message": "I'm sorry, but call transfers are not available in text chat tests.",
                        "action": "transfer_failed",
                        "reason": "textchat_not_supported",
                    }
                    await self._handle_transfer_result(
                        textchat_error_result, function_call_params, properties
                    )
                    return
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

                # Get organization ID for resolver/provider configuration
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

                external_pbx_call = (
                    getattr(workflow_run, "initial_context", None) or {}
                ).get("external_pbx_call")
                # Compatibility for calls that started before the migration.
                external_pbx_call = external_pbx_call or (
                    getattr(workflow_run, "initial_context", None) or {}
                ).get("upstream_pbx")
                if external_pbx_call:
                    # Context-to-in-group and lead-field mappings must see the
                    # final conversation-derived values.
                    await self._engine.perform_final_variable_extraction()

                resolver = config.get("resolver") if isinstance(config, dict) else None
                is_dynamic_transfer = config.get(
                    "destination_source", "static"
                ) == "dynamic" and isinstance(resolver, dict)

                if is_dynamic_transfer and resolver.get("wait_message"):
                    await self._engine.task.queue_frame(
                        TTSSpeakFrame(
                            str(resolver["wait_message"]),
                            append_to_context=False,
                            persist_to_logs=True,
                        )
                    )

                try:
                    resolved_transfer = await resolve_transfer_config(
                        tool=tool,
                        config=config,
                        arguments=function_call_params.arguments or {},
                        call_context_vars=self._engine._call_context_vars,
                        gathered_context_vars=self._engine._gathered_context,
                        organization_id=organization_id,
                        workflow_run_id=self._engine._workflow_run_id,
                    )
                    destination = resolved_transfer.destination
                    timeout_seconds = resolved_transfer.timeout_seconds
                except TransferResolutionError as e:
                    validation_error_result = {
                        "status": "failed",
                        "message": "I'm sorry, but I couldn't find a valid destination for this transfer.",
                        "action": "transfer_failed",
                        "reason": e.reason,
                    }
                    await self._handle_transfer_result(
                        validation_error_result, function_call_params, properties
                    )
                    return

                if (
                    resolved_transfer.source == "context_mapping"
                    and not external_pbx_call
                ):
                    await self._handle_transfer_result(
                        {
                            "status": "failed",
                            "message": (
                                "This call did not arrive through the configured "
                                "external PBX."
                            ),
                            "action": "transfer_failed",
                            "reason": "external_pbx_call_required",
                        },
                        function_call_params,
                        properties,
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

                provider = await get_telephony_provider_for_run(
                    workflow_run, organization_id
                )

                if resolved_transfer.message:
                    await self._engine.task.queue_frame(
                        TTSSpeakFrame(
                            resolved_transfer.message,
                            append_to_context=False,
                            persist_to_logs=True,
                        )
                    )
                else:
                    await self._play_config_message(config)

                if external_pbx_call:
                    workflow_configurations = (
                        await db_client.get_workflow_run_configurations(
                            self._engine._workflow_run_id, organization_id
                        )
                    )
                    field_updates = resolve_external_pbx_field_mappings(
                        self._engine._gathered_context,
                        workflow_configurations.get("external_pbx_field_mappings", []),
                    )
                    external_result = await provider.transfer_external_pbx_call(
                        identity=external_pbx_call,
                        destination=destination,
                        field_updates=field_updates,
                    )
                    if external_result is not None:
                        if external_result.get("status") == "success":
                            self._engine._gathered_context[
                                "external_pbx_transferred"
                            ] = True
                            await db_client.update_workflow_run(
                                run_id=self._engine._workflow_run_id,
                                gathered_context={"external_pbx_transferred": True},
                            )
                            await function_call_params.result_callback(
                                external_result, properties=properties
                            )
                            # Let VICIdial redirect the customer out of its
                            # conference before Dograh tears down the local leg.
                            await asyncio.sleep(4)
                            await self._engine.end_call_with_reason(
                                EndTaskReason.END_CALL_TOOL_REASON.value,
                                abort_immediately=True,
                            )
                        else:
                            await self._handle_transfer_result(
                                {
                                    **external_result,
                                    "action": "transfer_failed",
                                },
                                function_call_params,
                                properties,
                            )
                        return

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
                try:
                    masked_destination = (
                        f"***{destination[-4:]}" if len(destination) > 4 else "***"
                    )
                    logger.info(
                        "Transfer provider call starting "
                        f"source={resolved_transfer.source} "
                        f"resolution_id={resolved_transfer.resolution_id or ''} "
                        f"destination={masked_destination} timeout={timeout_seconds}"
                    )
                    transfer_result = await provider.transfer_call(
                        destination=destination,
                        transfer_id=transfer_id,
                        conference_name=conference_name,
                        timeout=timeout_seconds,
                    )
                except Exception as e:
                    logger.error(f"Transfer provider failed: {e}")
                    self._engine.set_mute_pipeline(False)
                    await call_transfer_manager.remove_transfer_context(transfer_id)
                    provider_error_result = {
                        "status": "failed",
                        "message": f"Transfer provider failed: {e}",
                        "action": "transfer_failed",
                        "reason": "provider_error",
                    }
                    await self._handle_transfer_result(
                        provider_error_result, function_call_params, properties
                    )
                    return

                call_sid = transfer_result.get("call_sid")
                logger.info(f"Transfer call initiated successfully: {call_sid}")

                # Update transfer context with actual call_sid from provider response
                transfer_context.call_sid = call_sid
                await call_transfer_manager.store_transfer_context(transfer_context)

                # Wait for status callback completion using Redis pub/sub
                logger.info(
                    "Transfer call initiated "
                    f"destination={masked_destination} transfer_id={transfer_id}, "
                    "waiting for completion..."
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
                    "message": result.get("message") or "Transfer failed",
                }
            )
        else:
            # Unknown action, treat as generic success
            logger.warning(f"Unknown transfer action: {action}, treating as success")
            await function_call_params.result_callback(result)
