"""Dograh integration for Pipecat's AWS Nova 2 Sonic service.

Nova Sonic owns STT, inference, and speech output. This subclass adapts the
service to Dograh's workflow lifecycle:

- replace muted input audio with silence to keep Nova's stream active;
- use the engine's initial ``TTSSpeakFrame``/``LLMContextFrame`` as a native
  Nova response trigger;
- accept ephemeral ``LLMMessagesAppendFrame`` prompts such as idle checks;
- defer workflow-control tools until the current audio response is complete;
- reconnect after node transitions because Nova cannot update prompts or tools
  on an active session; and
- mark Nova's completed user transcriptions as final.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from loguru import logger

from api.services.pipecat.realtime.conversation import RealtimeConversationMixin
from api.services.pipecat.realtime.static_greeting import format_static_greeting_prompt
from pipecat.adapters.services.aws_nova_sonic_adapter import (
    AWSNovaSonicConversationHistoryMessage,
    AWSNovaSonicLLMAdapter,
    AWSNovaSonicLLMInvocationParams,
    Role,
)
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    LLMMessagesAppendFrame,
    TranscriptionFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.aws.nova_sonic.llm import AWSNovaSonicLLMService
from pipecat.services.llm_service import FunctionCallFromLLM

_INITIAL_RESPONSE_PROMPT = (
    "The call has just connected. Begin the conversation now in a natural "
    "spoken voice, following your instructions, then wait for the caller."
)
_NODE_TRANSITION_RESPONSE_PROMPT = (
    "Continue the conversation now, following your current instructions."
)


class DograhAWSNovaSonicLLMAdapter(AWSNovaSonicLLMAdapter):
    """Keep bot-first conversations valid when replaying history to Nova."""

    def get_llm_invocation_params(
        self, context: LLMContext, *, system_instruction: str | None = None
    ) -> AWSNovaSonicLLMInvocationParams:
        params = super().get_llm_invocation_params(
            context, system_instruction=system_instruction
        )
        messages = params["messages"]
        history: list[AWSNovaSonicConversationHistoryMessage] = []
        if messages and messages[0].role is Role.ASSISTANT:
            # The greeting trigger is intentionally absent from Dograh's shared
            # transcript. Restore a historical USER prompt only in Nova's view:
            # AWS rejects history beginning with ASSISTANT. Keeping the greeting
            # also preserves what the caller is replying to after a reconnect.
            history.append(
                AWSNovaSonicConversationHistoryMessage(
                    role=Role.USER, text=_INITIAL_RESPONSE_PROMPT
                )
            )
        for message in messages:
            # Aggregation and filtering tool entries can leave adjacent messages
            # with the same role; Nova's history requires alternating roles.
            if history and history[-1].role is message.role:
                history[-1].text += "\n" + message.text
            else:
                history.append(replace(message))
        params["messages"] = history
        return params


class DograhAWSNovaSonicLLMService(RealtimeConversationMixin, AWSNovaSonicLLMService):
    """AWS Nova 2 Sonic with Dograh workflow integration."""

    adapter_class = DograhAWSNovaSonicLLMAdapter

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._input_content_lock = asyncio.Lock()
        # Set for the length of an open that follows a recorded greeting, so
        # nothing sent while connecting can ask Nova for a turn.
        self._opening_after_prerecorded_greeting: bool = False
        self._pending_initial_prompt: str | None = None
        self._pending_message_batches: list[tuple[list[tuple[Role, str]], bool]] = []
        self._deferred_node_transition_function_calls: list[FunctionCallFromLLM] = []
        self._awaiting_node_transition_context = False

    # ------------------------------------------------------------------
    # Frame handling
    # ------------------------------------------------------------------

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, LLMMessagesAppendFrame):
            await self._handle_messages_append(frame)
            return
        await super().process_frame(frame, direction)

    async def push_frame(
        self,
        frame: Frame,
        direction: FrameDirection = FrameDirection.DOWNSTREAM,
    ):
        # Nova emits this frame only after receiving FINAL transcript content.
        if isinstance(frame, TranscriptionFrame):
            frame.finalized = True
        await super().push_frame(frame, direction)

    async def _handle_initial_greeting(self, context: LLMContext, greeting_text: str):
        await self._handle_initial_prompt(
            context, format_static_greeting_prompt(greeting_text)
        )

    async def _handle_input_audio_frame(self, frame: InputAudioRawFrame):
        # Nova buffers input for session continuation inside this hook, before
        # sending it. Silence must also reach that replay buffer.
        await super()._handle_input_audio_frame(await self._prepare_user_audio(frame))

    async def send_text(
        self, text: str, role: str, prompt_name: str, stream: Any, interactive: bool
    ):
        # Audio system frames run alongside context updates. Keep each text
        # contentStart/textInput/contentEnd block together on the wire.
        async with self._input_content_lock:
            await super().send_text(text, role, prompt_name, stream, interactive)

    async def send_audio(
        self, audio: bytes, prompt_name: str, content_name: str, stream: Any
    ):
        async with self._input_content_lock:
            await super().send_audio(audio, prompt_name, content_name, stream)

    async def _send_user_audio_event(self, audio: bytes) -> None:
        if self._disconnecting or not self._stream or not self._audio_input_started:
            return

        stream = self._stream
        try:
            await super()._send_user_audio_event(audio)
        except Exception as e:
            # A send already in progress can fail after teardown starts, even
            # after a replacement stream has connected. Like Gemini Live,
            # ignore teardown failures while reporting active-session errors.
            if self._disconnecting or self._stream is not stream:
                return
            await self.push_error(error_msg=f"Send error: {e}", exception=e)

    # ------------------------------------------------------------------
    # Initial and one-off response triggers
    # ------------------------------------------------------------------

    async def _handle_context(self, context: LLMContext):
        if self._disconnecting:
            return

        self._context = context
        if self._awaiting_node_transition_context:
            await self._reconnect_for_node_transition(context)
            return

        if not self._handled_initial_context:
            self._handled_initial_context = True
            if self._pending_initial_prompt is None:
                self._pending_initial_prompt = _INITIAL_RESPONSE_PROMPT
            await self._finish_connecting_if_context_available()
            await self._flush_pending_text_inputs()
            return

        if not self._connected_time:
            # Error recovery reconnects before restoring context. Initialize the
            # new session and mark historical tool results as already handled.
            await self._finish_connecting_if_context_available()
            return

        await self._process_completed_function_calls(send_new_results=True)

    async def _open_after_prerecorded_greeting(self, transcript: str | None):
        """Connect and seed the greeting without letting Nova take a turn.

        Two things would otherwise have it speak over the recording. Upstream's
        connect path holds back a trailing USER message and re-sends it with
        ``interactive=True``, which is Nova's "generate now" signal -- and the
        context does end on a user turn whenever the answer supervisor releases
        after the callee has already spoken. On top of that, the greeting batch
        only flushes once that connect returns, so the trigger would land
        before the model had been told what the recording said.

        Suppressing interactive for the length of the open closes both:
        nothing sent while connecting can ask for a turn, whatever order it
        goes in.
        """
        if self._disconnecting:
            return
        if transcript:
            self._pending_message_batches.append(
                (self._prerecorded_greeting_turns(transcript), False)
            )
        self._opening_after_prerecorded_greeting = True
        try:
            await self._finish_connecting_if_context_available()
            await self._flush_pending_text_inputs()
        finally:
            self._opening_after_prerecorded_greeting = False

    def _prerecorded_greeting_turns(self, transcript: str) -> list[tuple[Role, str]]:
        """The greeting as Nova history: never opening on ASSISTANT, alternating.

        AWS rejects history that begins with ASSISTANT and requires roles to
        alternate, so the greeting needs a user turn to answer -- unless the
        conversation already supplied one, in which case adding another would
        put two USER turns back to back.
        """
        messages = []
        if self._context is not None:
            messages = self.get_llm_adapter().get_llm_invocation_params(self._context)[
                "messages"
            ]
        turns: list[tuple[Role, str]] = []
        if not messages or messages[-1].role is not Role.USER:
            turns.append((Role.USER, _INITIAL_RESPONSE_PROMPT))
        turns.append((Role.ASSISTANT, transcript))
        return turns

    async def _send_text_event(self, text: str, role: Role, interactive: bool = False):
        # `interactive=True` is what asks Nova to generate. While opening after
        # a recording the caller has already been greeted, so nothing sent may
        # ask for a turn -- including the trailing user message upstream
        # deliberately replays to trigger one.
        if self._opening_after_prerecorded_greeting:
            interactive = False
        await super()._send_text_event(text, role, interactive=interactive)

    async def _handle_initial_prompt(self, context: LLMContext | None, prompt: str):
        if context is None:
            logger.warning(
                f"{self}: received initial response trigger before context was set"
            )
            return

        self._handled_initial_context = True
        self._context = context
        self._pending_initial_prompt = prompt
        await self._finish_connecting_if_context_available()
        await self._flush_pending_text_inputs()

    async def _finish_connecting_if_context_available(self):
        # Upstream assumes this method is invoked once. Dograh can reach it from
        # connection-ready, runtime settings, and the initial response trigger.
        if self._connected_time:
            await self._flush_pending_text_inputs()
            return
        await super()._finish_connecting_if_context_available()
        await self._flush_pending_text_inputs()

    async def _handle_messages_append(self, frame: LLMMessagesAppendFrame):
        if self._disconnecting:
            return

        messages: list[tuple[Role, str]] = []
        for message in frame.messages:
            converted = self._convert_appended_message(message)
            if converted:
                messages.append(converted)

        if not messages:
            return

        self._pending_message_batches.append((messages, frame.run_llm))
        await self._flush_pending_text_inputs()

    def _convert_appended_message(
        self, message: dict[str, Any]
    ) -> tuple[Role, str] | None:
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

        # Nova only accepts SYSTEM content during prompt setup. Treat later
        # system/developer append frames as user instructions, matching its
        # universal-context adapter.
        return Role.USER, text

    @staticmethod
    def _extract_text_content(content: Any) -> str | None:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "text":
                    return None
                text = part.get("text")
                if not isinstance(text, str):
                    return None
                parts.append(text)
            return "\n".join(parts) if parts else None
        return None

    async def _flush_pending_text_inputs(self):
        if (
            self._disconnecting
            or self._awaiting_node_transition_context
            or not self._audio_input_started
        ):
            return

        if self._pending_initial_prompt is not None:
            prompt = self._pending_initial_prompt
            self._pending_initial_prompt = None
            await self._send_text_event(prompt, Role.USER, interactive=True)

        while self._pending_message_batches:
            messages, run_llm = self._pending_message_batches.pop(0)
            for index, (role, text) in enumerate(messages):
                is_last = index == len(messages) - 1
                await self._send_text_event(
                    text,
                    role,
                    interactive=run_llm and is_last,
                )

    # ------------------------------------------------------------------
    # Settings and node-transition lifecycle
    # ------------------------------------------------------------------

    async def _update_settings(self, delta: AWSNovaSonicLLMService.Settings):
        # Skip upstream Nova's blanket "not applied" warning; this subclass
        # explicitly handles the node prompt by reconnecting at the right point.
        system_instruction_was_given = "system_instruction" in delta.given_fields()
        changed = await super(AWSNovaSonicLLMService, self)._update_settings(delta)

        if system_instruction_was_given:
            if self._handled_initial_context:
                # Each Dograh node supplies its prompt, even if two adjacent
                # prompts happen to have identical text. Its tool set may still
                # have changed, so always refresh the Nova session.
                self._awaiting_node_transition_context = True
            else:
                # The engine pre-populates context before setting the first node
                # prompt. Complete prompt/session setup now if AWS is ready.
                await self._finish_connecting_if_context_available()

        handled = {"system_instruction"}
        self._warn_unhandled_updated_settings(changed.keys() - handled)
        return changed

    def _requires_node_transition_context_aggregation(self) -> bool:
        # Wait until the triggering transcript is committed before set_node()
        # changes the prompt and this service reconnects from local context.
        return True

    async def _reconnect_for_node_transition(self, context: LLMContext):
        logger.debug(
            f"{self}: reconnecting Nova Sonic with updated node prompt and tools"
        )

        # Upstream makes the last seeded USER message interactive, which starts
        # the new node's response. If the retained history ends with ASSISTANT
        # (common when a transition follows spoken text), queue a short user
        # nudge so the fresh session still responds exactly once.
        adapter = self.get_llm_adapter()
        invocation_params = adapter.get_llm_invocation_params(
            context,
            system_instruction=self._settings.system_instruction,
        )
        messages = invocation_params["messages"]
        if not messages or messages[-1].role is not Role.USER:
            self._pending_initial_prompt = _NODE_TRANSITION_RESPONSE_PROMPT

        await self._disconnect()

        # Intentional node changes seed the authoritative Dograh context below.
        # Drop the helper's old provider-native copy so its next timed session
        # continuation cannot replay both histories.
        conversation_history = getattr(self._sc, "_conversation_history", None)
        if isinstance(conversation_history, list):
            conversation_history.clear()

        self._context = context
        self._awaiting_node_transition_context = False
        await self._start_connecting()
        if not self._connected_time:
            # _start_connecting reports connection failures as ErrorFrames.
            # Retain the gate so a later context refresh cannot be sent to a
            # half-configured session.
            self._awaiting_node_transition_context = True

    # ------------------------------------------------------------------
    # Workflow-control deferral
    # ------------------------------------------------------------------

    async def run_function_calls(self, function_calls: Sequence[FunctionCallFromLLM]):
        has_node_transition = any(
            self._function_is_node_transition(call.function_name)
            for call in function_calls
        )
        if self._assistant_is_responding and has_node_transition:
            self._deferred_node_transition_function_calls.extend(function_calls)
            logger.debug(
                f"{self}: deferring {len(function_calls)} workflow-control "
                "call(s) until the Nova audio turn ends"
            )
            return
        await super().run_function_calls(function_calls)

    async def _report_assistant_response_ended(self):
        await super()._report_assistant_response_ended()
        await self._run_deferred_node_transition_function_calls()

    async def _handle_completion_end_event(self, event_json):
        await super()._handle_completion_end_event(event_json)
        if not self._assistant_is_responding:
            await self._run_deferred_node_transition_function_calls()

    async def _run_deferred_node_transition_function_calls(self):
        if not self._deferred_node_transition_function_calls:
            return
        function_calls = self._deferred_node_transition_function_calls
        self._deferred_node_transition_function_calls = []
        logger.debug(
            f"{self}: executing {len(function_calls)} deferred workflow-control "
            "call(s) after the Nova audio turn ended"
        )
        await super().run_function_calls(function_calls)
