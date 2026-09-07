"""Dograh integration for Pipecat's AWS Nova 2 Sonic service.

Nova Sonic owns STT, inference, and speech output. This subclass adapts the
service to Dograh's workflow lifecycle:

- gate input audio while the user is muted;
- use the engine's initial ``TTSSpeakFrame``/``LLMContextFrame`` as a native
  Nova response trigger;
- accept ephemeral ``LLMMessagesAppendFrame`` prompts such as idle checks;
- defer workflow-control tools until the current audio response is complete;
- reconnect after node transitions because Nova cannot update prompts or tools
  on an active session; and
- mark Nova's completed user transcriptions as final.
"""

from collections.abc import Sequence
from typing import Any

from loguru import logger

from api.services.pipecat.realtime.static_greeting import format_static_greeting_prompt
from pipecat.adapters.services.aws_nova_sonic_adapter import Role
from pipecat.frames.frames import (
    Frame,
    LLMMessagesAppendFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserMuteStartedFrame,
    UserMuteStoppedFrame,
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


class DograhAWSNovaSonicLLMService(AWSNovaSonicLLMService):
    """AWS Nova 2 Sonic with Dograh workflow integration."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._user_is_muted = False
        # Dograh assigns ``_context`` before the first LLMContextFrame, so its
        # presence cannot identify whether the initial response already ran.
        self._handled_initial_context = False
        self._pending_initial_prompt: str | None = None
        self._pending_message_batches: list[tuple[list[tuple[Role, str]], bool]] = []
        self._deferred_node_transition_function_calls: list[FunctionCallFromLLM] = []
        self._awaiting_node_transition_context = False

    # ------------------------------------------------------------------
    # Frame handling
    # ------------------------------------------------------------------

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
            # Nova renders its own audio, so consume the engine's initial TTS
            # trigger and turn it into an interactive text prompt.
            if not self._handled_initial_context:
                greeting = frame.text.strip() if frame.text else ""
                prompt = (
                    format_static_greeting_prompt(greeting)
                    if greeting
                    else _INITIAL_RESPONSE_PROMPT
                )
                await self._handle_initial_prompt(self._context, prompt)
            else:
                logger.warning(
                    f"{self}: TTSSpeakFrame after initial context already handled — "
                    "Nova Sonic owns audio generation, ignoring"
                )
            return
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

    async def _handle_input_audio_frame(self, frame):
        if self._user_is_muted:
            return
        await super()._handle_input_audio_frame(frame)

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

        await self._process_completed_function_calls(send_new_results=True)

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
