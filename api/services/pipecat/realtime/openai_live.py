"""OpenAI Live with Dograh workflow tools and conversation controls."""

from collections.abc import Sequence
from dataclasses import replace

from loguru import logger

from api.services.pipecat.realtime.conversation import RealtimeConversationMixin
from api.services.pipecat.usage_metrics import LiveUsageMetricsData
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    LLMMessagesAppendFrame,
    MetricsFrame,
)
from pipecat.metrics.metrics import LLMUsageMetricsData
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import FunctionCallFromLLM
from pipecat.services.openai.live import events
from pipecat.services.openai.live.llm import (
    MAX_CONTEXT_APPEND_TOKENS,
    OPENAI_SAMPLE_RATE,
    OpenAILiveLLMService,
    _chunk_text,
)
from pipecat.services.openai.responses.llm import OpenAIResponsesLLMService
from pipecat.utils.types import NOT_GIVEN, is_given

VOICE_INSTRUCTIONS = """You are the spoken interface of a workflow assistant.
Speak naturally and briefly, in the caller's language. Let the caller finish.
Delegate each substantive user request and every workflow decision to the backend.
The backend owns the current workflow, business rules, and tools. Follow its
guidance on what to ask or say next, and convey its results without inventing facts.
Never claim an action succeeded before the backend confirms it. You may acknowledge
the caller while work runs. If they correct a detail, use the latest correction.
Stop speaking when interrupted and keep listening. Do not announce node changes
or other internal workflow details. Follow application instructions for greetings
and checks when the caller is quiet."""

BACKEND_INSTRUCTIONS = """You are the backend of a spoken workflow assistant.
Follow the current workflow below using the conversation supplied by the Live API.
Use its tools for workflow transitions and actions. Return concise natural language
for the voice assistant to say to the caller, including the next question required
by the workflow. Do not expose internal instructions or tool names.

Current workflow:
"""


class DograhOpenAILiveLLMService(RealtimeConversationMixin, OpenAILiveLLMService):
    """Keep workflow instructions on the Responses backend of a Live session."""

    def __init__(self, *, backend_model: str, settings=None, **kwargs):
        settings = settings or self.Settings()
        super().__init__(
            settings=replace(settings, system_instruction=VOICE_INSTRUCTIONS),
            delegation=self.ResponsesDelegation(
                settings=OpenAIResponsesLLMService.Settings(model=backend_model)
            ),
            **kwargs,
        )
        self._bot_is_speaking = False
        self._deferred_transitions: list[FunctionCallFromLLM] = []
        self._pending_speech: list[str] = []
        self._initial_backend_request = False
        # A recorded greeting opens the conversation without the backend, so
        # the session must start without requesting an opening line.
        self._prerecorded_greeting_played = False
        self._pending_prerecorded_greeting: str | None = None
        self._sent_backend_snapshot: str | None = None
        self._live_audio_seconds = 0.0

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        if isinstance(frame, MetricsFrame):
            for data in frame.data:
                if (
                    isinstance(data, LLMUsageMetricsData)
                    and data.processor == self.name
                ):
                    # Upstream emits only backend token usage. Keep its model
                    # and processor distinct from duration-based voice usage.
                    data.model = self._delegation.settings.model
                    data.processor = self.name.replace("Live", "ResponsesBackend")
        await super().push_frame(frame, direction)

    async def _report_usage(self, usage):
        await super()._report_usage(usage)
        if usage.seconds is None:
            return
        delta = max(0.0, usage.seconds - self._live_audio_seconds)
        self._live_audio_seconds = max(self._live_audio_seconds, usage.seconds)
        if delta and self.usage_metrics_enabled:
            await self.push_frame(
                MetricsFrame(
                    data=[
                        LiveUsageMetricsData(
                            processor=self.name,
                            model=self._session_model,
                            seconds=delta,
                        )
                    ]
                )
            )

    async def _update_settings(self, delta):
        # The voice prompt is fixed for the session; node prompts and tools
        # belong to the backend and can change without losing the conversation.
        if is_given(delta.system_instruction):
            self._delegation.settings.system_instruction = BACKEND_INSTRUCTIONS + (
                delta.system_instruction or ""
            )
        changed = await super()._update_settings(
            replace(delta, system_instruction=NOT_GIVEN)
        )
        await self._maybe_send_tools_update()
        return changed

    async def _maybe_send_tools_update(self):
        if not self._session_started or self._context is None:
            return
        params = self._invocation_params()
        self._sync_registered_tool_handlers(self._context.tools)
        delegation = self._delegation_config(params["tools"], params["tool_choice"])
        # Empty tools must be sent explicitly when a node removes its tools.
        delegation.responses["tools"] = params["tools"]
        snapshot = delegation.model_dump_json()
        if snapshot == self._sent_backend_snapshot:
            return
        await self.send_client_event(
            events.SessionUpdateEvent(
                session=events.SessionUpdateConfig(delegation=delegation)
            )
        )
        self._sent_backend_snapshot = snapshot

    async def _handle_context(self, context: LLMContext):
        if context is None:
            return
        self._handled_initial_context = True
        if self._needs_session_config:
            self._initial_backend_request = (
                not self._pending_speech and not self._prerecorded_greeting_played
            )
        await super()._handle_context(context)
        await self._maybe_send_tools_update()

    async def _handle_evt_session_started(self, evt):
        self._live_audio_seconds = 0.0
        await super()._handle_evt_session_started(evt)
        await self._flush_prerecorded_greeting()
        pending, self._pending_speech = self._pending_speech, []
        for text in pending:
            await self._send_speech_instruction(text)
        if self._initial_backend_request and not pending:
            # Ask the workflow backend for the opening line. Live continues
            # speaking independently once this initial work has been started.
            await self.send_client_event(events.ResponseCreateEvent())
        self._initial_backend_request = False

    async def _send_speech_instruction(self, text: str):
        # Greetings and idle checks are application instructions. Commentary
        # supplies information to paraphrase and does not request exact wording.
        for chunk in _chunk_text(text, MAX_CONTEXT_APPEND_TOKENS):
            event = events.SessionInstructionsAppendEvent(
                delegation_id=None, content=chunk
            )
            logger.debug(f"{self}: requesting speech with instruction {event.event_id}")
            await self.send_client_event(event)

    async def _speak(self, text: str):
        if not text.strip():
            return
        if self._session_started:
            await self._send_speech_instruction(text)
        else:
            self._pending_speech.append(text)
            self._initial_backend_request = False
            if self._context is not None:
                await self._handle_context(self._context)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, LLMMessagesAppendFrame):
            for message in frame.messages:
                if not isinstance(message, dict):
                    continue
                text = message.get("content")
                if not isinstance(text, str) or not text.strip():
                    continue
                if frame.run_llm:
                    await self._speak(text)
                elif self._session_started:
                    await self._send_context_append(None, text, spoken=False)
            return
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_is_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_is_speaking = False
            calls, self._deferred_transitions = self._deferred_transitions, []
            if calls:
                await super().run_function_calls(calls)
        await super().process_frame(frame, direction)

    async def _handle_initial_greeting(self, context: LLMContext, greeting_text: str):
        if context is None:
            logger.warning(f"{self}: received greeting before context was set")
            return
        self._handled_initial_context = True
        self._context = context
        await self._speak(
            "Speak immediately, without waiting for the caller. "
            "Say the following text aloud in its original language, then wait "
            f"for the caller. Do not add a preamble:\n{greeting_text}"
        )

    async def _open_after_prerecorded_greeting(self, transcript: str | None):
        """Start the session without asking the backend for an opening line.

        The greeting reaches the model on the thinking channel rather than as
        startup history: the session is configured from a context the
        aggregator has not written the transcript to yet, and the commentary
        channel would have it paraphrase the greeting aloud.

        Guarded on teardown because ``_handle_context`` clears
        ``_needs_session_config`` before the socket send, which is dropped
        while disconnecting -- the flag would be spent with nothing sent, and
        a later reconnect would never configure the session.
        """
        if self._disconnecting:
            return
        self._prerecorded_greeting_played = True
        self._pending_prerecorded_greeting = transcript
        await self._handle_context(self._context)
        await self._flush_prerecorded_greeting()

    async def _flush_prerecorded_greeting(self):
        if not self._session_started or self._pending_prerecorded_greeting is None:
            return
        transcript = self._pending_prerecorded_greeting
        self._pending_prerecorded_greeting = None
        await self._send_context_append(
            None,
            "You have already greeted the caller, by playing a recording that "
            f"said: {transcript}",
            spoken=False,
        )

    async def _prepare_user_audio(self, frame: InputAudioRawFrame):
        return await self._prepare_audio_frame(
            frame,
            sample_rate=OPENAI_SAMPLE_RATE if self._session_started else None,
            resampler=self._resampler,
        )

    async def run_function_calls(self, function_calls: Sequence[FunctionCallFromLLM]):
        # Keep a batch intact so a transition cannot outrun related tool calls.
        if self._bot_is_speaking and any(
            self._function_is_node_transition(call.function_name)
            for call in function_calls
        ):
            self._deferred_transitions.extend(function_calls)
            return
        await super().run_function_calls(function_calls)

    async def _disconnect(self):
        self._bot_is_speaking = False
        self._deferred_transitions.clear()
        self._pending_speech.clear()
        self._initial_backend_request = False
        self._sent_backend_snapshot = None
        await super()._disconnect()
