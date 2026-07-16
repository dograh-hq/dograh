"""Dograh subclass of pipecat's Ultravox realtime LLM service.

Ultravox is audio-native and realtime. Its native call stages allow a client
tool result to atomically change the system prompt and tools while preserving
the call's server-side conversation history. This wrapper adapts that model to
the Dograh engine contract by:

- deferring the first call creation until the engine queues the initial node
  opening via ``TTSSpeakFrame`` or ``LLMContextFrame``
- returning node-transition tool results with ``responseType="new-stage"`` so
  the existing call keeps its complete audio-native history
- updating the next stage's system prompt and selected tools without a
  disconnect/reconnect cycle
- deferring workflow-control tools until any active Ultravox response ends
- handling Dograh-only frames such as user mute and idle append prompts
- tagging user transcripts with ``finalized=True`` for downstream parity
"""

import hashlib
import json
from typing import Any

from loguru import logger
from pydantic import Field
from websockets.exceptions import ConnectionClosed

from pipecat.frames.frames import (
    Frame,
    LLMMessagesAppendFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserMuteStartedFrame,
    UserMuteStoppedFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext, is_given
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService
from pipecat.services.settings import _NotGiven, assert_given
from pipecat.services.ultravox.llm import (
    OneShotInputParams,
    UltravoxRealtimeLLMService,
    websocket_client,
)
from pipecat.utils.time import time_now_iso8601


class DograhUltravoxOneShotInputParams(OneShotInputParams):
    """Dograh-friendly OneShot params with string voice support."""

    voice: str | None = Field(default=None)


_ULTRAVOX_MAX_TOOL_TIMEOUT_SECS = 40.0


class DograhUltravoxRealtimeLLMService(UltravoxRealtimeLLMService):
    """Ultravox realtime with Dograh engine integration quirks."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._context: LLMContext | None = None
        self._selected_tools = None
        self._user_is_muted: bool = False
        self._call_started: bool = False
        self._stage_update_required: bool = False
        # Ultravox applies a stage update on the matching client tool result,
        # so retain the provider invocation ID until that result reaches us via
        # the context aggregator. Unlike Gemini, this ID is part of the wire
        # protocol needed to update the existing call without reconnecting.
        self._pending_node_transition_tool_call_ids: set[str] = set()
        # A stage result can replace the active prompt and tools immediately.
        # Hold transition invocations separately so ordinary tools can still
        # run during speech while workflow control waits for response end.
        self._deferred_node_transition_tool_invocations: list[
            tuple[str, str, dict[str, Any]]
        ] = []
        self._pending_user_text_messages: list[str] = []

    async def start(self, frame):
        # Dograh defers call creation until the engine queues the node opening.
        await LLMService.start(self, frame)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, UserMuteStartedFrame):
            self._user_is_muted = True
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, UserMuteStoppedFrame):
            self._user_is_muted = False
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, TTSSpeakFrame):
            if not self._socket:
                await self._connect_call(
                    greeting_text=frame.text,
                    agent_speaks_first=True,
                )
            else:
                logger.warning(
                    f"{self}: TTSSpeakFrame received after the Ultravox call was "
                    "already created; ignoring because Ultravox owns speech output"
                )
            return
        if isinstance(frame, LLMMessagesAppendFrame):
            await self._handle_messages_append(frame)
            return
        await super().process_frame(frame, direction)

    async def _update_settings(self, delta: UltravoxRealtimeLLMService.Settings):
        changed = await super(UltravoxRealtimeLLMService, self)._update_settings(delta)
        if "output_medium" in changed:
            await self._update_output_medium(assert_given(self._settings.output_medium))
        if "system_instruction" in changed and self._socket:
            # The updated instruction is included in the native new-stage
            # response when the transition tool result reaches _handle_context.
            self._stage_update_required = True
        handled = {"output_medium", "system_instruction"}
        self._warn_unhandled_updated_settings(changed.keys() - handled)
        return changed

    async def _disconnect(self):
        self._disconnecting = True
        await self.stop_all_metrics()
        if self._socket:
            await self._socket.close()
            self._socket = None
        if self._receive_task:
            await self.cancel_task(self._receive_task, timeout=1.0)
            self._receive_task = None
        self._completed_tool_calls = set()
        self._call_started = False
        self._started_placeholder_sent = set()
        self._pending_node_transition_tool_call_ids = set()
        self._deferred_node_transition_tool_invocations = []
        self._disconnecting = False

    async def _send_user_audio(self, frame):
        if self._user_is_muted:
            return
        await super()._send_user_audio(frame)

    async def _handle_context(self, context: LLMContext):
        self._context = context

        if not self._socket:
            await self._connect_call(
                greeting_text=None,
                agent_speaks_first=True,
            )
            return

        current_tools = self._current_tools_schema(context)
        if self._pending_node_transition_tool_call_ids and self._tools_changed(
            current_tools
        ):
            self._stage_update_required = True
        await super()._handle_context(context)

    async def _handle_messages_append(self, frame: LLMMessagesAppendFrame):
        texts = [
            text
            for text in (
                self._extract_text_content(message.get("content"))
                for message in frame.messages
                if isinstance(message, dict)
            )
            if text
        ]
        if not texts:
            return

        if not self._socket:
            self._pending_user_text_messages.extend(texts)
            await self._connect_call(
                greeting_text=None,
                agent_speaks_first=False,
            )
            return

        if not self._call_started:
            self._pending_user_text_messages.extend(texts)
            logger.debug(
                f"{self}: queueing {len(texts)} user text message(s) until call_started"
            )
            return

        for text in texts:
            await self._send_user_text(text)

    async def _handle_user_transcript(self, text: str):
        transcript = text.strip() if text else ""
        if not transcript:
            return
        await self.broadcast_frame(
            TranscriptionFrame,
            user_id=self._last_user_id or "",
            timestamp=time_now_iso8601(),
            result=text,
            text=transcript,
            finalized=True,
        )

    def _requires_node_transition_context_aggregation(self) -> bool:
        """Commit any received final user transcript before changing stages.

        Ultravox preserves its own audio-native history across a stage change,
        but Dograh's local context still needs the final transcript before the
        transition handler updates the workflow node.
        """
        return True

    async def _handle_tool_invocation(
        self, tool_name: str, invocation_id: str, parameters: dict[str, Any]
    ):
        if self._function_is_node_transition(tool_name):
            self._pending_node_transition_tool_call_ids.add(invocation_id)
            if self._bot_responding:
                self._deferred_node_transition_tool_invocations.append(
                    (tool_name, invocation_id, parameters)
                )
                logger.debug(
                    f"{self}: deferring workflow-control call {tool_name} "
                    "until bot turn ends"
                )
                return
        await super()._handle_tool_invocation(tool_name, invocation_id, parameters)

    async def _handle_response_end(self):
        """Close the current response before applying queued workflow control."""
        await super()._handle_response_end()
        await self._run_deferred_node_transition_tool_invocations()

    async def _run_deferred_node_transition_tool_invocations(self):
        if not self._deferred_node_transition_tool_invocations:
            return

        invocations = self._deferred_node_transition_tool_invocations
        self._deferred_node_transition_tool_invocations = []
        logger.debug(
            f"{self}: executing {len(invocations)} deferred workflow-control "
            "call(s) after bot turn ended"
        )
        for tool_name, invocation_id, parameters in invocations:
            await super()._handle_tool_invocation(tool_name, invocation_id, parameters)

    async def _send_tool_result(self, tool_call_id: str, result: str):
        is_node_transition = tool_call_id in self._pending_node_transition_tool_call_ids
        try:
            if is_node_transition and self._stage_update_required:
                await self._send_node_transition_stage_result(tool_call_id, result)
            else:
                await super()._send_tool_result(tool_call_id, result)
        finally:
            if is_node_transition:
                self._pending_node_transition_tool_call_ids.discard(tool_call_id)

    async def _send_node_transition_stage_result(self, tool_call_id: str, result: str):
        """Apply node settings using Ultravox's native call-stage protocol."""
        next_tools = self._current_tools_schema(self._context)
        stage = {
            "systemPrompt": self._current_system_instruction(),
            "selectedTools": self._selected_tools_payload(next_tools),
            # Keep the workflow handler's result as the tool-result message in
            # the inherited conversation history for the next generation.
            "toolResultText": result,
        }
        logger.debug(
            f"{self}: updating Ultravox call stage for tool_call_id={tool_call_id} "
            f"with {len(stage['selectedTools'])} selected tool(s)"
        )
        await self._send(
            {
                "type": "client_tool_result",
                "invocationId": tool_call_id,
                "result": json.dumps(stage, ensure_ascii=True, default=str),
                "responseType": "new-stage",
            }
        )
        self._selected_tools = next_tools
        self._stage_update_required = False

    async def _connect_call(
        self,
        *,
        greeting_text: str | None,
        agent_speaks_first: bool,
    ):
        params = self._build_one_shot_params(
            greeting_text=greeting_text,
            agent_speaks_first=agent_speaks_first,
        )
        self._params = params
        self._selected_tools = self._current_tools_schema(self._context)
        tool_names = (
            [tool.name for tool in self._selected_tools.standard_tools]
            if self._selected_tools
            else []
        )
        prompt = params.system_prompt or ""
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]

        try:
            logger.info(
                f"{self}: creating Ultravox call "
                f"(agent_speaks_first={agent_speaks_first}, "
                f"voice={params.voice!r}, "
                f"tools={tool_names}, "
                f"system_prompt_len={len(prompt)}, "
                f"system_prompt_sha256={prompt_hash})"
            )
            join_url = await self._start_one_shot_call(params)
            logger.info(f"Joining Ultravox Realtime call via URL: {join_url}")
            self._socket = await websocket_client.connect(join_url)
            self._receive_task = self.create_task(self._receive_messages())
            self._call_started = False
        except Exception as e:
            logger.error(
                f"{self}: Ultravox call creation/join failed "
                f"for tools={tool_names}: {e}"
            )
            await self.push_error(f"Failed to connect to Ultravox: {e}", e, fatal=True)

    async def _receive_messages(self):
        """Receive messages from the Ultravox Realtime WebSocket.

        Upstream handles exceptions raised while processing individual messages,
        but websocket close exceptions are raised by the async iterator itself.
        During user hangup / pipeline teardown that close is expected, so treat
        normal websocket shutdown as a debug condition rather than a pipeline
        error.
        """
        if not self._socket:
            return

        try:
            async for message in self._socket:
                try:
                    if isinstance(message, bytes):
                        await self._handle_audio(message)
                        continue

                    data = json.loads(message)
                    match data.get("type"):
                        case "call_started":
                            self._call_started = True
                            logger.debug(
                                f"{self}: Ultravox call_started received for callId="
                                f"{data.get('callId')}"
                            )
                            await self._flush_pending_user_text_messages()
                        case "state":
                            if self._bot_responding and data.get("state") != "speaking":
                                await self._handle_response_end()
                        case "client_tool_invocation":
                            await self._handle_tool_invocation(
                                data.get("toolName"),
                                data.get("invocationId"),
                                data.get("parameters"),
                            )
                        case "transcript":
                            match data.get("role"):
                                case "user":
                                    if not data.get("final"):
                                        logger.warning(
                                            "Unexpected non-final user transcript from Ultravox Realtime; ignoring."
                                        )
                                    else:
                                        await self._handle_user_transcript(
                                            data.get("text")
                                        )
                                case "agent":
                                    await self._handle_agent_transcript(
                                        data.get("medium"),
                                        data.get("text"),
                                        data.get("delta"),
                                        data.get("final", False),
                                    )
                                case _:
                                    logger.debug(
                                        f"Received transcript with unknown role from Ultravox Realtime: {data}"
                                    )
                        case _:
                            logger.debug(f"Received unhandled Ultravox message: {data}")
                except Exception as e:
                    if self._disconnecting or not self._socket:
                        return
                    await self.push_error(
                        "Ultravox websocket receive error", e, fatal=True
                    )
        except ConnectionClosed as e:
            if (
                self._disconnecting
                or not self._socket
                or self._is_benign_websocket_close(e)
            ):
                logger.debug(f"{self}: Ultravox websocket closed: {e}")
                return
            await self.push_error("Ultravox websocket receive error", e, fatal=True)

    async def _flush_pending_user_text_messages(self):
        if (
            not self._socket
            or not self._call_started
            or not self._pending_user_text_messages
        ):
            return

        pending_texts = self._pending_user_text_messages
        self._pending_user_text_messages = []
        for pending_text in pending_texts:
            await self._send_user_text(pending_text)

    def _build_one_shot_params(
        self,
        *,
        greeting_text: str | None,
        agent_speaks_first: bool,
    ) -> DograhUltravoxOneShotInputParams:
        current_params = self._params
        extra = {
            key: value
            for key, value in current_params.extra.items()
            if key != "firstSpeakerSettings"
        }

        if greeting_text is not None:
            extra["firstSpeakerSettings"] = {"agent": {"text": greeting_text}}
        elif agent_speaks_first:
            extra["firstSpeakerSettings"] = {"agent": {}}
        else:
            extra["firstSpeakerSettings"] = {"user": {}}
        output_medium = self._settings.output_medium
        if isinstance(output_medium, _NotGiven):
            output_medium = current_params.output_medium

        return DograhUltravoxOneShotInputParams(
            api_key=current_params.api_key,
            system_prompt=self._current_system_instruction(),
            temperature=current_params.temperature,
            model=assert_given(self._settings.model),
            voice=current_params.voice,
            metadata=current_params.metadata,
            output_medium=output_medium,
            max_duration=current_params.max_duration,
            extra=extra,
        )

    def _current_tools_schema(self, context: LLMContext | None):
        if context is None or not is_given(context.tools):
            return None
        return context.tools

    def _selected_tools_payload(self, tools: Any) -> list[dict[str, Any]]:
        return self._to_selected_tools(tools) if tools else []

    def _tools_changed(self, tools: Any) -> bool:
        return self._selected_tools_payload(tools) != self._selected_tools_payload(
            self._selected_tools
        )

    def _to_selected_tools(self, tool: Any) -> list[dict[str, Any]]:
        selected_tools = super()._to_selected_tools(tool)
        for selected_tool in selected_tools:
            temporary_tool = selected_tool.get("temporaryTool")
            if not isinstance(temporary_tool, dict):
                continue

            tool_name = temporary_tool.get("modelToolName")
            if not isinstance(tool_name, str):
                continue

            timeout = self._ultravox_timeout_for_tool(tool_name)
            if timeout is not None:
                temporary_tool["timeout"] = timeout
        return selected_tools

    def _current_system_instruction(self) -> str | None:
        system_instruction = self._settings.system_instruction
        if isinstance(system_instruction, _NotGiven):
            return None
        return system_instruction

    def _ultravox_timeout_for_tool(self, function_name: str) -> str | None:
        item = self._functions.get(function_name) or self._functions.get(None)
        if item is None or item.timeout_secs is None or item.timeout_secs <= 0:
            return None

        timeout_secs = min(float(item.timeout_secs), _ULTRAVOX_MAX_TOOL_TIMEOUT_SECS)
        return f"{timeout_secs:g}s"

    @staticmethod
    def _is_benign_websocket_close(exc: ConnectionClosed) -> bool:
        return any(
            close is not None and close.code in {1000, 1001}
            for close in (exc.sent, exc.rcvd)
        )

    @staticmethod
    def _extract_text_content(content: Any) -> str | None:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if not isinstance(part, dict):
                    return None
                if part.get("type") != "text":
                    return None
                text = part.get("text")
                if not isinstance(text, str):
                    return None
                parts.append(text)
            return "\n".join(parts) if parts else None
        return None
