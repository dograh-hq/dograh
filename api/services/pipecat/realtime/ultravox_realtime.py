"""Dograh subclass of pipecat's Ultravox realtime LLM service.

Ultravox is audio-native and realtime, but prompt and tool configuration is
bound to call creation. Dograh therefore cannot lean on in-session updates or
Gemini-style session resumption handles. This wrapper adapts Ultravox to the
Dograh engine contract by:

- deferring the first call creation until the engine queues the initial node
  opening via ``TTSSpeakFrame`` or ``LLMContextFrame``
- marking the call for recreation when ``system_instruction`` changes across
  node transitions, then rebuilding it on the follow-up ``LLMContextFrame``
  so the transition tool result is present in ``initialMessages``
- reconstructing Ultravox ``initialMessages`` from Dograh context when the
  call must be recreated after a node transition
- appending a transient resumptive user nudge to recreated ``initialMessages``
  after tool-result transitions, without mutating Dograh's stored context
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
from pipecat.processors.aggregators import async_tool_messages
from pipecat.processors.aggregators.llm_context import (
    LLMContext,
    LLMSpecificMessage,
    is_given,
)
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
_RESUMPTION_USER_MESSAGE = (
    "IMPORTANT: We are resuming an existing conversation. You are given previous turns ONLY for your reference. "
    "Do not use that to frame your response. Follow your ORIGINAL INSTRUCTIONS ONLY."
)


class DograhUltravoxRealtimeLLMService(UltravoxRealtimeLLMService):
    """Ultravox realtime with Dograh engine integration quirks."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._context: LLMContext | None = None
        self._selected_tools = None
        self._user_is_muted: bool = False
        self._call_system_instruction: str | None = None
        self._reconnect_required: bool = False
        self._call_started: bool = False
        self._has_connected_once: bool = False
        self._pending_reconnect_system_instruction: str | None = None
        self._pending_initial_messages: list[dict[str, Any]] | None = None
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
                    system_instruction=self._current_system_instruction(),
                    greeting_text=frame.text,
                    initial_messages=None,
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
        if "system_instruction" in changed and self._has_connected_once:
            # Mirror Gemini's "settings change means reconnect" intent, but
            # defer the actual new-call creation until the subsequent
            # LLMContextFrame arrives with the transition tool result. Ultravox
            # cannot accept that historical tool result over a formal
            # post-connect tool-response channel the way Gemini can.
            self._reconnect_required = True
        handled = {"output_medium", "system_instruction"}
        self._warn_unhandled_updated_settings(changed.keys() - handled)
        return changed

    async def _disconnect(self, preserve_completed_tool_calls: bool = True):
        self._disconnecting = True
        await self.stop_all_metrics()
        if self._socket:
            await self._socket.close()
            self._socket = None
        if self._receive_task:
            await self.cancel_task(self._receive_task, timeout=1.0)
            self._receive_task = None
        if not preserve_completed_tool_calls:
            self._completed_tool_calls = set()
        self._call_started = False
        self._started_placeholder_sent = set()
        self._disconnecting = False

    async def _send_user_audio(self, frame):
        if self._user_is_muted:
            return
        await super()._send_user_audio(frame)

    async def _handle_context(self, context: LLMContext):
        self._context = context
        system_instruction = self._current_system_instruction()

        if self._socket and not self._reconnect_required:
            await super()._handle_context(context)
            return

        initial_messages, history_tool_call_ids = self._build_initial_messages(context)
        if history_tool_call_ids:
            self._completed_tool_calls.update(history_tool_call_ids)

        if self._bot_responding:
            self._pending_reconnect_system_instruction = system_instruction
            self._pending_initial_messages = initial_messages
            return

        await self._reconnect_with_context(
            system_instruction=system_instruction,
            initial_messages=initial_messages,
        )

    async def _handle_response_end(self):
        await super()._handle_response_end()
        if self._pending_reconnect_system_instruction is None:
            return

        system_instruction = self._pending_reconnect_system_instruction
        initial_messages = self._pending_initial_messages
        self._pending_reconnect_system_instruction = None
        self._pending_initial_messages = None
        await self._reconnect_with_context(
            system_instruction=system_instruction,
            initial_messages=initial_messages,
        )

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
                system_instruction=self._current_system_instruction(),
                greeting_text=None,
                initial_messages=None,
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

    async def _connect_call(
        self,
        *,
        system_instruction: str | None,
        greeting_text: str | None,
        initial_messages: list[dict[str, Any]] | None,
        agent_speaks_first: bool,
    ):
        params = self._build_one_shot_params(
            greeting_text=greeting_text,
            initial_messages=initial_messages,
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
            self._call_system_instruction = system_instruction
            self._call_started = False
            self._has_connected_once = True
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

    async def _reconnect_with_context(
        self,
        *,
        system_instruction: str | None,
        initial_messages: list[dict[str, Any]] | None,
    ):
        call_initial_messages = self._initial_messages_for_call(initial_messages)
        logger.debug(
            f"{self}: reconnecting Ultravox call with initialMessages="
            f"{json.dumps(call_initial_messages, ensure_ascii=True, default=str)}"
        )
        if self._socket:
            await self._disconnect(preserve_completed_tool_calls=True)

        await self._connect_call(
            system_instruction=system_instruction,
            greeting_text=None,
            initial_messages=initial_messages,
            agent_speaks_first=self._should_agent_speak_first(initial_messages),
        )
        self._reconnect_required = False

    def _build_one_shot_params(
        self,
        *,
        greeting_text: str | None,
        initial_messages: list[dict[str, Any]] | None,
        agent_speaks_first: bool,
    ) -> DograhUltravoxOneShotInputParams:
        current_params = self._params
        extra = {
            key: value
            for key, value in current_params.extra.items()
            if key not in {"firstSpeakerSettings", "initialMessages"}
        }

        if greeting_text is not None:
            extra["firstSpeakerSettings"] = {"agent": {"text": greeting_text}}
        elif agent_speaks_first:
            extra["firstSpeakerSettings"] = {"agent": {}}
        else:
            extra["firstSpeakerSettings"] = {"user": {}}
        call_initial_messages = self._initial_messages_for_call(initial_messages)
        if call_initial_messages:
            extra["initialMessages"] = call_initial_messages

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

    def _initial_messages_for_call(
        self, initial_messages: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]] | None:
        if not initial_messages:
            return None
        if not self._should_add_resumption_user_message(initial_messages):
            return initial_messages

        return [
            *initial_messages,
            {
                "role": "MESSAGE_ROLE_USER",
                "text": _RESUMPTION_USER_MESSAGE,
            },
        ]

    def _build_initial_messages(
        self, context: LLMContext
    ) -> tuple[list[dict[str, Any]] | None, set[str]]:
        initial_messages: list[dict[str, Any]] = []
        tool_call_id_to_name: dict[str, str] = {}
        completed_tool_call_ids: set[str] = set()

        for message in context.get_messages():
            if isinstance(message, LLMSpecificMessage):
                continue

            async_payload = async_tool_messages.parse_message(message)
            if async_payload is not None:
                if async_payload.kind == "intermediate":
                    logger.error(
                        f"{self}: Ultravox does not support streamed async tool results; "
                        f"dropping intermediate result from initialMessages for "
                        f"tool_call_id={async_payload.tool_call_id}."
                    )
                    continue
                if async_payload.kind == "final":
                    initial_message = self._build_ultravox_message(
                        role="MESSAGE_ROLE_TOOL_RESULT",
                        text=async_payload.result or "",
                        invocation_id=async_payload.tool_call_id,
                        tool_name=tool_call_id_to_name.get(async_payload.tool_call_id),
                    )
                    if initial_message is not None:
                        initial_messages.append(initial_message)
                    completed_tool_call_ids.add(async_payload.tool_call_id)
                continue

            role = message.get("role")
            if role == "user":
                initial_message = self._build_ultravox_message(
                    role="MESSAGE_ROLE_USER",
                    text=self._extract_text_content(message.get("content")),
                )
                if initial_message is not None:
                    initial_messages.append(initial_message)
            elif role == "assistant":
                text = self._extract_text_content(message.get("content"))
                initial_message = self._build_ultravox_message(
                    role="MESSAGE_ROLE_AGENT",
                    text=text,
                )
                if initial_message is not None:
                    initial_messages.append(initial_message)

                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list):
                    for tool_call in tool_calls:
                        if not isinstance(tool_call, dict):
                            continue
                        tool_id = tool_call.get("id")
                        function = tool_call.get("function")
                        tool_name = (
                            function.get("name") if isinstance(function, dict) else None
                        )
                        if isinstance(tool_id, str) and isinstance(tool_name, str):
                            tool_call_id_to_name[tool_id] = tool_name
                            initial_message = self._build_ultravox_message(
                                role="MESSAGE_ROLE_TOOL_CALL",
                                text="",
                                invocation_id=tool_id,
                                tool_name=tool_name,
                            )
                            if initial_message is not None:
                                initial_messages.append(initial_message)
            elif (
                role == "tool"
                and message.get("content") != "IN_PROGRESS"
                and message.get("content") != "CANCELLED"
            ):
                tool_call_id = message.get("tool_call_id")
                initial_message = self._build_ultravox_message(
                    role="MESSAGE_ROLE_TOOL_RESULT",
                    text=self._stringify_tool_result(message.get("content")),
                    invocation_id=tool_call_id
                    if isinstance(tool_call_id, str)
                    else None,
                    tool_name=(
                        tool_call_id_to_name.get(tool_call_id)
                        if isinstance(tool_call_id, str)
                        else None
                    ),
                )
                if initial_message is not None:
                    initial_messages.append(initial_message)
                if isinstance(tool_call_id, str):
                    completed_tool_call_ids.add(tool_call_id)

        return (initial_messages or None), completed_tool_call_ids

    @staticmethod
    def _build_ultravox_message(
        *,
        role: str,
        text: str | None,
        invocation_id: str | None = None,
        tool_name: str | None = None,
    ) -> dict[str, Any] | None:
        if text is None:
            return None

        message: dict[str, Any] = {
            "role": role,
            "text": text,
        }
        if invocation_id is not None:
            message["invocationId"] = invocation_id
        if tool_name is not None:
            message["toolName"] = tool_name
        return message

    @staticmethod
    def _should_agent_speak_first(
        initial_messages: list[dict[str, Any]] | None,
    ) -> bool:
        if not initial_messages:
            return True
        return initial_messages[-1].get("role") in {
            "MESSAGE_ROLE_USER",
            "MESSAGE_ROLE_TOOL_RESULT",
        }

    @staticmethod
    def _should_add_resumption_user_message(
        initial_messages: list[dict[str, Any]] | None,
    ) -> bool:
        if not initial_messages:
            return False
        return initial_messages[-1].get("role") == "MESSAGE_ROLE_TOOL_RESULT"

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

    @staticmethod
    def _stringify_tool_result(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            if parts:
                return "".join(parts)
        return json.dumps(content, ensure_ascii=True, default=str)
