"""Dograh subclass of pipecat's OpenAI Realtime LLM service.

Layers Dograh engine integration quirks onto upstream-pristine
:class:`OpenAIRealtimeLLMService`. Substantially smaller than the Gemini
subclass because OpenAI Realtime supports runtime ``session.update`` for
both ``system_instruction`` and tools — no reconnect/defer-tool-call
machinery needed.

Adds:

- **User-mute audio gating** via ``UserMuteStarted/StoppedFrame``.
- **TTSSpeakFrame as initial-response trigger** so the engine's greeting
  flow kicks off the bot's first response.
- **finalized=True on TranscriptionFrame** for parity with the Gemini
  service (every OpenAI transcription via the ``completed`` event is
  final by construction).
"""

import json

from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserMuteStartedFrame,
    UserMuteStoppedFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import FunctionCallFromLLM
from pipecat.services.openai.realtime.llm import OpenAIRealtimeLLMService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601


class DograhOpenAIRealtimeLLMService(OpenAIRealtimeLLMService):
    """OpenAI Realtime with Dograh engine integration quirks. See module docstring."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._user_is_muted: bool = False
        # Dograh pre-populates self._context via the engine before the first
        # LLMContextFrame arrives, so upstream's "first arrival means
        # self._context is None" check no longer works.
        self._handled_initial_context: bool = False
        # Track bot speech locally so tool calls can be deferred until the bot
        # has finished speaking, matching Dograh's Gemini Live behavior.
        self._bot_is_speaking: bool = False
        self._deferred_function_calls: list[FunctionCallFromLLM] = []

    # ------------------------------------------------------------------
    # Frame handling: mute, TTSSpeakFrame as greeting trigger
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
            # Greeting trigger: the engine queues a TTSSpeakFrame after node
            # setup. OpenAI Realtime renders its own audio, so we don't pass
            # the frame to TTS. Route through _handle_context so the initial
            # response and later tool-result turns share the same context
            # lifecycle even when Dograh has already pre-populated self._context.
            if not self._handled_initial_context:
                await self._handle_context(self._context)
            else:
                logger.warning(
                    f"{self}: TTSSpeakFrame after initial context already "
                    "handled — OpenAI Realtime owns audio generation, ignoring"
                )
            # Don't forward the frame; the audio path is owned by the realtime
            # service itself.
            return
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_is_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_is_speaking = False
            await self._run_pending_function_calls()
        await super().process_frame(frame, direction)

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

    async def _send_user_audio(self, frame):
        if self._user_is_muted:
            return
        await super()._send_user_audio(frame)

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
        """Process or defer tool calls until the bot finishes speaking."""
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
                logger.warning(
                    f"Available pending calls: {list(self._pending_function_calls.keys())}"
                )

        except Exception as e:
            logger.error(f"Failed to process function call arguments: {e}")

    # ------------------------------------------------------------------
    # Transcription: broadcast with finalized=True for parity with the
    # Gemini service (consumers that check `finalized` should see True
    # for every completed-transcription event from OpenAI).
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
