"""Dograh subclass of pipecat's OpenAI Realtime LLM service.

Layers Dograh engine integration quirks onto upstream-pristine
:class:`OpenAIRealtimeLLMService`. Substantially smaller than the Gemini
subclass because OpenAI Realtime supports runtime ``session.update`` for
both ``system_instruction`` and tools, so node changes do not require a
reconnect.

Adds:

- **Silent audio while muted** via ``UserMuteStarted/StoppedFrame``.
- **TTSSpeakFrame as initial-response trigger** so the engine's greeting
  flow kicks off the bot's first response.
- **Silent session setup after a recorded greeting**, which the engine plays
  straight to the transport instead of routing through the model.
- **One-off LLMMessagesAppendFrame handling** for ephemeral realtime prompts
  like user-idle checks, without mutating Dograh's local ``LLMContext``.
- **Workflow-control deferral** so node transitions, call termination, and
  transfers wait for any current bot audio to finish while ordinary tools run
  immediately.
- **finalized=True on TranscriptionFrame** because every OpenAI
  transcription via the ``completed`` event is final by construction.
"""

import json
from typing import Any

from loguru import logger

from api.services.pipecat.realtime.conversation import RealtimeConversationMixin
from api.services.pipecat.realtime.static_greeting import format_static_greeting_prompt
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    TranscriptionFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import FunctionCallFromLLM
from pipecat.services.openai.realtime import events
from pipecat.services.openai.realtime.llm import (
    OPENAI_SAMPLE_RATE,
    OpenAIRealtimeLLMService,
)
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601


class DograhOpenAIRealtimeLLMService(
    RealtimeConversationMixin, OpenAIRealtimeLLMService
):
    """OpenAI Realtime with Dograh engine integration quirks. See module docstring."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Track bot speech locally so workflow-control calls can wait until the
        # bot has finished speaking without delaying ordinary tools.
        self._bot_is_speaking: bool = False
        self._deferred_node_transition_function_calls: list[FunctionCallFromLLM] = []
        self._pending_initial_greeting_text: str | None = None
        # A recorded greeting can open the conversation before the API session
        # is ready; its seed then waits for session.updated. Kept separate from
        # the text-greeting slot because the transcript may be None while the
        # open itself is still pending.
        self._pending_prerecorded_greeting: tuple[str | None] | None = None

    # ------------------------------------------------------------------
    # Provider frames: ephemeral prompts and tool-call deferral
    # ------------------------------------------------------------------

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, LLMMessagesAppendFrame):
            await self._handle_messages_append(frame)
            return
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_is_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_is_speaking = False
            await self._run_pending_node_transition_function_calls()
        await super().process_frame(frame, direction)

    async def _handle_messages_append(self, frame: LLMMessagesAppendFrame):
        """Consume a one-off append frame without mutating the local LLMContext."""
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

    async def _open_after_prerecorded_greeting(self, transcript: str | None):
        """Configure the session and seed the greeting, without a response.

        The greeting is sent as its own assistant item rather than through the
        context: the realtime adapter only passes a lone *user* message
        through untouched, and packs anything else into a synthetic "this is a
        previously saved conversation" user turn that ends by telling the model
        to say it is ready to continue.
        """
        if self._disconnecting:
            return

        if not self._api_session_ready:
            self._pending_prerecorded_greeting = (transcript,)
            return

        self._pending_prerecorded_greeting = None
        await self._ensure_conversation_setup()
        if not transcript:
            return

        evt = events.ConversationItemCreateEvent(
            item=events.ConversationItem(
                type="message",
                role="assistant",
                content=[events.ItemContent(type="output_text", text=transcript)],
            )
        )
        self._messages_added_manually[evt.item.id] = True
        await self.send_client_event(evt)

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
        elif self._pending_prerecorded_greeting is not None:
            await self._open_after_prerecorded_greeting(
                *self._pending_prerecorded_greeting
            )

    async def _prepare_user_audio(self, frame):
        properties = self._settings.session_properties
        audio_input = properties.audio.input if properties.audio else None
        audio_format = audio_input.format if audio_input else None
        encoding = audio_format.type if audio_format else "audio/pcm"
        # G.711 sessions carry encoded samples rather than signed PCM.
        silence_byte = {"audio/pcmu": 0xFF, "audio/pcma": 0xD5}.get(encoding, 0)
        return await self._prepare_audio_frame(
            frame,
            sample_rate=OPENAI_SAMPLE_RATE if encoding == "audio/pcm" else None,
            resampler=self._input_resampler,
            silence_byte=silence_byte,
        )

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
        """Trigger inference after manually appending conversation items."""
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

    async def _run_pending_node_transition_function_calls(self):
        if not self._deferred_node_transition_function_calls:
            return
        function_calls = self._deferred_node_transition_function_calls
        self._deferred_node_transition_function_calls = []
        logger.debug(
            f"{self}: executing {len(function_calls)} deferred workflow-control "
            "call(s) after bot turn ended"
        )
        await self.run_function_calls(function_calls)

    async def _handle_evt_function_call_arguments_done(self, evt):
        """Run ordinary tools immediately and defer workflow-control calls."""
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

                is_node_transition = self._function_is_node_transition(
                    function_call_item.name
                )
                if self._bot_is_speaking and is_node_transition:
                    self._deferred_node_transition_function_calls.extend(function_calls)
                    logger.debug(
                        f"{self}: deferring workflow-control call "
                        f"{function_call_item.name} "
                        "until bot stops speaking"
                    )
                else:
                    await self.run_function_calls(function_calls)
                    logger.debug(f"Processed function call: {function_call_item.name}")
            else:
                logger.warning(
                    f"No tracked function call found for call_id: {evt.call_id}"
                )
                logger.warning(
                    f"Available pending calls: {list(self._pending_function_calls.keys())}"
                )

        except Exception as e:
            logger.error(f"Failed to process function call arguments: {e}")

    # ------------------------------------------------------------------
    # Transcription: broadcast with finalized=True for every
    # completed-transcription event from OpenAI.
    # ------------------------------------------------------------------

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
