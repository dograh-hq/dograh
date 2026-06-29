"""Dograh subclass of pipecat's Azure OpenAI Realtime LLM service.

Layers Dograh engine integration quirks (mute gating, TTSSpeakFrame greeting
trigger, LLMMessagesAppendFrame handling, deferred tool calls) onto pipecat's
AzureRealtimeLLMService, mirroring what DograhOpenAIRealtimeLLMService does
for the standard OpenAI Realtime endpoint.
"""

import json
from typing import Any

from loguru import logger

from api.services.pipecat.realtime.static_greeting import format_static_greeting_prompt
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserMuteStartedFrame,
    UserMuteStoppedFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.azure.realtime.llm import AzureRealtimeLLMService
from pipecat.services.llm_service import FunctionCallFromLLM
from pipecat.services.openai.realtime import events
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601


class DograhAzureRealtimeLLMService(AzureRealtimeLLMService):
    """Azure OpenAI Realtime with Dograh engine integration quirks.

    Extends AzureRealtimeLLMService with the same Dograh-specific behaviours
    added to DograhOpenAIRealtimeLLMService:
    - User-mute audio gating
    - TTSSpeakFrame as initial-response trigger
    - One-off LLMMessagesAppendFrame handling
    - Deferred tool calls until bot finishes speaking
    - finalized=True on TranscriptionFrame for consistency
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._user_is_muted: bool = False
        self._handled_initial_context: bool = False
        self._bot_is_speaking: bool = False
        self._deferred_function_calls: list[FunctionCallFromLLM] = []
        self._pending_initial_greeting_text: str | None = None

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
            if not self._handled_initial_context:
                greeting_text = frame.text.strip() if frame.text else ""
                if greeting_text:
                    await self._handle_initial_greeting(self._context, greeting_text)
                else:
                    await self._handle_context(self._context)
            else:
                logger.warning(
                    f"{self}: TTSSpeakFrame after initial context already handled — "
                    "Azure Realtime owns audio generation, ignoring"
                )
            return
        if isinstance(frame, LLMMessagesAppendFrame):
            await self._handle_messages_append(frame)
            return
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_is_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_is_speaking = False
            await self._run_pending_function_calls()
        await super().process_frame(frame, direction)

    async def _handle_messages_append(self, frame: LLMMessagesAppendFrame):
        if self._disconnecting:
            return

        if not self._api_session_ready:
            if frame.run_llm:
                logger.debug(
                    f"{self}: LLMMessagesAppendFrame received before session ready; "
                    "deferring response until the session is initialized"
                )
                self._run_llm_when_api_session_ready = True
            return

        appended_any = False
        for message in frame.messages:
            item = self._message_to_conversation_item(message)
            if item is None:
                continue
            evt = events.ConversationItemCreateEvent(item=item)
            self._messages_added_manually[evt.item.id] = True
            await self.send_client_event(evt)
            appended_any = True

        if frame.run_llm and appended_any:
            await self._send_manual_response_create()

    async def _handle_context(self, context: LLMContext):
        if not self._handled_initial_context:
            if context is None:
                logger.warning(
                    f"{self}: received initial context trigger before context was set"
                )
                return
            self._handled_initial_context = True
            self._context = context
            await self._create_response()
        else:
            self._context = context
            await self._process_completed_function_calls(send_new_results=True)

    async def _handle_initial_greeting(self, context: LLMContext, greeting_text: str):
        if context is None:
            logger.warning(
                f"{self}: received initial greeting trigger before context was set"
            )
            return

        self._handled_initial_context = True
        self._context = context
        await self._create_initial_greeting_response(greeting_text)

    async def _create_initial_greeting_response(self, greeting_text: str):
        if self._disconnecting:
            return

        if not self._api_session_ready:
            self._pending_initial_greeting_text = greeting_text
            self._run_llm_when_api_session_ready = True
            return

        self._pending_initial_greeting_text = None
        await self._ensure_conversation_setup()
        await self._send_manual_response_create(
            instructions=format_static_greeting_prompt(greeting_text),
            tool_choice="none",
        )

    async def _ensure_conversation_setup(self):
        if not self._llm_needs_conversation_setup:
            return

        adapter = self.get_llm_adapter()
        llm_invocation_params = adapter.get_llm_invocation_params(self._context)
        for item in llm_invocation_params["messages"]:
            evt = events.ConversationItemCreateEvent(item=item)
            self._messages_added_manually[evt.item.id] = True
            await self.send_client_event(evt)

        await self._send_session_update()
        self._llm_needs_conversation_setup = False

    async def _handle_evt_session_updated(self, evt):
        self._api_session_ready = True
        if self._pending_initial_greeting_text is not None:
            greeting_text = self._pending_initial_greeting_text
            self._run_llm_when_api_session_ready = False
            await self._create_initial_greeting_response(greeting_text)
        elif self._run_llm_when_api_session_ready:
            self._run_llm_when_api_session_ready = False
            await self._create_response()

    async def _send_user_audio(self, frame):
        if self._user_is_muted:
            return
        await super()._send_user_audio(frame)

    def _message_to_conversation_item(
        self, message: dict[str, Any]
    ) -> events.ConversationItem | None:
        if not isinstance(message, dict):
            logger.warning(
                f"{self}: skipping unsupported appended message payload {message!r}"
            )
            return None

        role = message.get("role")
        if role not in {"user", "system", "developer"}:
            logger.warning(
                f"{self}: skipping unsupported appended message role {role!r}"
            )
            return None

        text = self._extract_text_content(message.get("content"))
        if not text:
            logger.warning(
                f"{self}: skipping appended message with unsupported content {message!r}"
            )
            return None

        item_role = "system" if role in {"system", "developer"} else "user"
        return events.ConversationItem(
            type="message",
            role=item_role,
            content=[events.ItemContent(type="input_text", text=text)],
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

    async def _send_manual_response_create(
        self,
        *,
        instructions: str | None = None,
        tool_choice: str | None = None,
    ):
        await self.push_frame(LLMFullResponseStartFrame())
        await self.start_processing_metrics()
        await self.start_ttfb_metrics()
        await self.send_client_event(
            events.ResponseCreateEvent(
                response=events.ResponseProperties(
                    output_modalities=self._get_enabled_modalities(),
                    instructions=instructions,
                    tool_choice=tool_choice,
                )
            )
        )

    async def _run_pending_function_calls(self):
        if not self._deferred_function_calls:
            return
        function_calls = self._deferred_function_calls
        self._deferred_function_calls = []
        logger.debug(
            f"{self}: executing {len(function_calls)} deferred function call(s) "
            "after bot turn ended"
        )
        await self.run_function_calls(function_calls)

    async def _handle_evt_function_call_arguments_done(self, evt):
        try:
            args = json.loads(evt.arguments)

            function_call_item = self._pending_function_calls.get(evt.call_id)
            if function_call_item:
                del self._pending_function_calls[evt.call_id]

                function_calls = [
                    FunctionCallFromLLM(
                        context=self._context,
                        tool_call_id=evt.call_id,
                        function_name=function_call_item.name,
                        arguments=args,
                    )
                ]

                if self._bot_is_speaking:
                    self._deferred_function_calls.extend(function_calls)
                    logger.debug(
                        f"{self}: deferring function call {function_call_item.name} "
                        "until bot stops speaking"
                    )
                else:
                    await self.run_function_calls(function_calls)
                    logger.debug(f"Processed function call: {function_call_item.name}")
            else:
                logger.warning(
                    f"No tracked function call found for call_id: {evt.call_id}"
                )
        except Exception as e:
            logger.error(f"Failed to process function call arguments: {e}")

    async def handle_evt_input_audio_transcription_completed(self, evt):
        await self._call_event_handler(
            "on_conversation_item_updated", evt.item_id, None
        )
        await self.broadcast_frame(
            TranscriptionFrame,
            text=evt.transcript,
            user_id="",
            timestamp=time_now_iso8601(),
            result=evt,
            finalized=True,
        )
        await self._handle_user_transcription(evt.transcript, True, Language.EN)
