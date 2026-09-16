"""Dograh subclass of pipecat's Gemini Live LLM service.

Layers Dograh engine integration quirks onto upstream-pristine
:class:`GeminiLiveLLMService`:

- **Deferred connect.** Connection is held back until ``system_instruction``
  is set via :meth:`_update_settings`, so pre-call-fetch template variables
  land before the live session opens.
- **Reconnect on node transitions.** Gemini Live cannot update
  ``system_instruction`` mid-session, so a setting change triggers a
  reconnect (deferred until the bot turn ends if currently responding).
- **Node-transition deferral.** Node-transition calls emitted mid-turn are
  queued and run when the bot stops speaking, to avoid cutting off its audio.
- **Silent audio while muted.** The shared conversation mixin replaces muted
  caller audio while preserving Gemini's readiness and activity windows.
- **TTSSpeakFrame as greeting trigger.** The engine queues a TTSSpeakFrame
  to kick off the first response after node setup; the service intercepts
  it and runs the initial-context path.
- **Silent open after a recorded greeting.** A configured audio greeting is
  played straight to the transport, so the engine opens the session through
  the shared mixin instead. The seed must not request a turn, or Gemini
  greets the caller a second time.
"""

import asyncio
from typing import Any, cast

from google.genai.types import Content, ContentDict, Part
from loguru import logger

from api.services.pipecat.gemini_json_schema_adapter import (
    DograhGeminiLiveJSONSchemaAdapter,
)
from api.services.pipecat.realtime.conversation import RealtimeConversationMixin
from api.services.pipecat.realtime.static_greeting import format_static_greeting_prompt
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    Frame,
    FunctionCallFromLLM,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService
from pipecat.utils.tracing.service_decorators import traced_gemini_live


class DograhGeminiLiveLLMService(RealtimeConversationMixin, GeminiLiveLLMService):
    """Gemini Live with Dograh engine integration quirks. See module docstring."""

    # Gemini input transcription is delivered independently from tool calls.
    # Give late transcription messages a small window to arrive before running
    # a node-transition function and tearing down the current Live connection.
    _NODE_TRANSITION_TRANSCRIPTION_GRACE_SECONDS = 0.5

    # Route tool schemas through Gemini's ``parameters_json_schema`` field so
    # MCP/imported tools that use JSON Schema keywords (``const``, ``not``,
    # nested ``anyOf``) rejected by the strict ``Schema`` model are accepted,
    # while keeping upstream's Live-specific tool-call-to-text conversion for
    # seeded contexts. Mirrors the non-realtime ``DograhGoogleLLMService`` fix;
    # ``DograhGeminiLiveVertexLLMService`` inherits this via MRO.
    adapter_class = DograhGeminiLiveJSONSchemaAdapter

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Node-transition calls emitted mid-bot-turn are deferred here so the
        # transition does not tear down Gemini while it is still producing audio.
        self._pending_node_transition_function_calls: list[FunctionCallFromLLM] = []
        # Text greeting captured from the first TTSSpeakFrame while the Gemini
        # session is still connecting.
        self._pending_initial_greeting_text: str | None = None
        self._transition_function_call_task: asyncio.Task | None = None
        # Intentional node changes use a fresh, context-seeded connection rather
        # than a potentially stale session-resumption handle. The new connection
        # remains gated until the function-call result has landed in LLMContext.
        self._awaiting_node_transition_context: bool = False
        self._node_transition_context_received: bool = False
        self._node_transition_context_seed_started: bool = False
        # Greeting a recording already delivered, held until a session exists.
        # Kept separate from the text-greeting slot because the transcript may
        # legitimately be None while the open itself is still pending.
        self._pending_prerecorded_greeting: tuple[str | None] | None = None
        # Set only by the base class's transient-error reconnect path. Dograh
        # pre-populates ``_context`` before its first Live session, so context
        # presence alone cannot distinguish an initial connection from a
        # reconnect that needs history re-seeding.
        self._reconnecting_after_error: bool = False

    # ------------------------------------------------------------------
    # Hooks from upstream GeminiLiveLLMService
    # ------------------------------------------------------------------

    def _should_connect_on_start(self) -> bool:
        # Hold the connection until the engine sets a system_instruction. This
        # lets pre-call fetch populate template variables first.
        return bool(self._settings.system_instruction)

    def _requires_node_transition_context_aggregation(self) -> bool:
        # A node transition replaces the current Gemini Live connection and
        # seeds the new one from our local LLMContext. Wait for the upstream
        # user aggregator to commit any final TranscriptionFrame before
        # set_node() changes the prompt and starts that reconnect.
        return True

    async def cleanup(self) -> None:
        """Cancel a delayed transition before tearing down the Live session."""
        if self._transition_function_call_task:
            await self.cancel_task(self._transition_function_call_task)
            self._transition_function_call_task = None
        await super().cleanup()

    async def _handle_changed_settings(self, changed: dict[str, Any]) -> set[str]:
        if "system_instruction" not in changed:
            return set()

        # PipecatEngine updates system_instruction only from set_node(). The
        # first set_node happens before a Live session exists; every later one
        # is a node transition whose tool call has already been deferred until
        # the current bot turn finishes.
        if not self._session:
            # First-time setting after deferred-connect.
            await self._connect()
        else:
            await self._reconnect_for_node_transition()
        return {"system_instruction"}

    async def _run_or_defer_function_calls(
        self, function_calls_llm: list[FunctionCallFromLLM]
    ):
        if not self._contains_node_transition(function_calls_llm):
            await super()._run_or_defer_function_calls(function_calls_llm)
            return

        # Keep a provider tool-call batch together. Splitting a mixed batch here
        # would discard Pipecat's shared function-call group and could trigger an
        # LLM run before every result from the original batch has arrived.
        if self._bot_is_responding:
            # Latest batch wins; Gemini emits tool calls as one batch per
            # tool_call message, so this overwrite is intentional.
            self._pending_node_transition_function_calls = function_calls_llm
            logger.debug(
                f"{self}: deferring {len(function_calls_llm)} node-transition "
                "function call(s) "
                "until bot turn ends"
            )
            return

        self._schedule_node_transition_function_calls(function_calls_llm)

    def _contains_node_transition(
        self, function_calls_llm: list[FunctionCallFromLLM]
    ) -> bool:
        return any(self._is_node_transition(fc) for fc in function_calls_llm)

    def _is_node_transition(self, function_call: FunctionCallFromLLM) -> bool:
        return self._function_is_node_transition(function_call.function_name)

    def _schedule_node_transition_function_calls(
        self, function_calls_llm: list[FunctionCallFromLLM]
    ) -> None:
        """Run transition calls after late input transcription has settled."""
        if (
            self._transition_function_call_task
            and not self._transition_function_call_task.done()
        ):
            logger.warning(
                f"{self}: node-transition function call already pending; "
                "ignoring duplicate batch"
            )
            return

        async def _run_after_transcription_grace() -> None:
            try:
                await asyncio.sleep(self._NODE_TRANSITION_TRANSCRIPTION_GRACE_SECONDS)
                await self._flush_pending_user_transcription()
                await self.run_function_calls(function_calls_llm)
            finally:
                self._transition_function_call_task = None

        self._transition_function_call_task = self.create_task(
            _run_after_transcription_grace(),
            name=f"{self}::node-transition-function-calls",
        )

    async def _flush_pending_user_transcription(self) -> None:
        """Publish any punctuationless user transcript before a node handoff."""
        if self._transcription_timeout_task:
            if not self._transcription_timeout_task.done():
                await self.cancel_task(self._transcription_timeout_task)
            self._transcription_timeout_task = None

        if not self._user_transcription_buffer:
            return

        text = self._user_transcription_buffer
        self._user_transcription_buffer = ""
        logger.debug(
            f"{self}: flushing pending user transcription before node transition"
        )
        await self._push_user_transcription(text, result=None)

    # ------------------------------------------------------------------
    # State-transition side effects
    # ------------------------------------------------------------------

    async def _set_bot_is_responding(self, responding: bool):
        was_responding = self._bot_is_responding
        await super()._set_bot_is_responding(responding)
        if was_responding and not responding:
            await self._run_pending_node_transition_function_calls()

    async def _run_pending_node_transition_function_calls(self):
        """Run any node-transition calls deferred during the bot's last turn."""
        if not self._pending_node_transition_function_calls:
            return
        fcs = self._pending_node_transition_function_calls
        self._pending_node_transition_function_calls = []
        logger.debug(
            f"{self}: executing {len(fcs)} deferred node-transition call(s) "
            "after bot turn ended"
        )
        self._schedule_node_transition_function_calls(fcs)

    async def _disconnect_for_reconnect(self) -> bool:
        """Disconnect without discarding a pending graceful shutdown.

        Returns:
            ``True`` when the caller should open a new Gemini session.
            ``False`` when a deferred :class:`EndFrame` was released instead.
        """
        await self._disconnect(preserve_pending_end_frame=True)
        if not self._end_frame_pending_bot_turn_finished:
            return True

        logger.info(
            "Releasing deferred EndFrame instead of reconnecting Gemini service"
        )
        await self._release_deferred_end_frame()
        return False

    async def _reconnect(self):
        """Mark transient reconnects so a no-handle session re-seeds history."""
        self._reconnecting_after_error = True
        await super()._reconnect()

    async def _reconnect_for_node_transition(self) -> None:
        """Start a fresh connection and wait to seed the completed context.

        Gemini can report ``resumable=False`` while generating or executing a
        function call. A workflow transition happens at exactly that boundary,
        so using the last (older) resumption handle can omit the triggering user
        turn. Use the local LLMContext as the source of truth for this intentional
        handoff instead.
        """
        self._awaiting_node_transition_context = True
        self._node_transition_context_received = False
        self._node_transition_context_seed_started = False
        self._session_resumption_handle = None
        should_open_new_session = await self._disconnect_for_reconnect()
        if not should_open_new_session:
            # The helper released a deferred EndFrame, so graceful shutdown now
            # owns the lifecycle and this node transition must not reconnect.
            self._awaiting_node_transition_context = False
            self._node_transition_context_received = False
            self._node_transition_context_seed_started = False
            return
        await self._connect(session_resumption_handle=None)

    # ------------------------------------------------------------------
    # Frame handling: mute, TTSSpeakFrame, BotStoppedSpeakingFrame flush
    # ------------------------------------------------------------------

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, BotStoppedSpeakingFrame):
            # Belt-and-suspenders: the main drain happens in
            # _set_bot_is_responding(False), but if Gemini delays turn_complete
            # past the audible end of the turn, flushing here ensures a pending
            # node transition fires promptly.
            await self._run_pending_node_transition_function_calls()
            # Fall through to super for the actual push.
        await super().process_frame(frame, direction)

    # ------------------------------------------------------------------
    # Context lifecycle: Dograh pre-populates self._context via the engine,
    # so upstream's "first arrival === self._context is None" check doesn't
    # work. We gate on _handled_initial_context instead and skip the
    # init-instruction reconciliation (Dograh updates system_instruction at
    # runtime via _update_settings, not via init).
    # ------------------------------------------------------------------

    async def _handle_context(self, context: LLMContext | None):
        if context is None:
            logger.warning(f"{self}: received context trigger before context was set")
            return
        if self._awaiting_node_transition_context:
            self._context = context
            self._node_transition_context_received = True
            await self._maybe_seed_node_transition_context()
            return
        if not self._handled_initial_context:
            self._handled_initial_context = True
            self._context = context
            await self._prepare_context_for_fresh_session()
            await self._create_initial_response()
        else:
            self._context = context
            await self._process_completed_function_calls(send_new_results=True)

    async def _prepare_context_for_fresh_session(self) -> None:
        """Account for tool results that will be represented in a history seed.

        Gemini Live converts historical tool calls and results to text before
        sending them through ``send_client_content``. A fresh session therefore
        must not also receive those old call IDs through ``send_tool_response``:
        unlike a resumed session, it never issued those calls. Mark the results
        represented by the seed as complete and discard any queued live replies.
        """
        await self._process_completed_function_calls(send_new_results=False)
        self._pending_tool_results.clear()

    async def _handle_initial_greeting(
        self, context: LLMContext | None, greeting_text: str
    ):
        """Trigger the first Gemini turn with an exact static text greeting."""
        if context is None:
            logger.warning(
                f"{self}: received initial greeting trigger before context was set"
            )
            return

        self._handled_initial_context = True
        self._context = context
        await self._create_initial_greeting_response(greeting_text)

    async def _open_after_prerecorded_greeting(self, transcript: str | None):
        """Seed the greeting the caller heard and open the input gate.

        Gemini Live drops every caller audio frame until an opening turn has
        set ``_ready_for_realtime_input``, so a recording played straight to
        the transport leaves the session deaf as well as unseeded.
        """
        await self._prepare_context_for_fresh_session()
        await self._create_prerecorded_greeting_response(transcript)

    async def _create_prerecorded_greeting_response(self, transcript: str | None):
        """Seed the spoken greeting, leaving the turn open for the caller.

        ``turn_complete=False`` is what separates this from every other
        opening: Gemini takes the history but is not asked to produce a turn,
        so it waits for the caller instead of greeting them again.
        """
        if self._disconnecting:
            return

        if not self._session:
            self._pending_prerecorded_greeting = (transcript,)
            self._run_llm_when_session_ready = True
            return

        self._pending_prerecorded_greeting = None

        adapter = self.get_llm_adapter()
        turns = cast(
            "list[Content | ContentDict]",
            list(adapter.get_llm_invocation_params(self._context).get("messages", [])),
        )
        if transcript:
            turns.append(Content(role="model", parts=[Part(text=transcript)]))
        if not self._is_gemini_3 and (
            not turns or getattr(turns[-1], "role", None) != "user"
        ):
            # Gemini 2.5 requires a seed to end on a user turn. Padding one
            # that already does would put two user turns back to back, which
            # happens with an untranscribed recording on a context the caller
            # has already spoken into.
            turns.append(Content(role="user", parts=[Part(text=" ")]))

        logger.debug("Seeding Gemini Live with a prerecorded greeting")

        try:
            if turns:
                await self._session.send_client_content(
                    turns=turns, turn_complete=False
                )
        except Exception as e:
            await self._handle_send_error(e)

        if not self._is_gemini_3:
            # 2.5 only picks seeded history up once a turn completes; let the
            # caller's first utterance carry that completion.
            self._needs_initial_turn_complete_message = True

        self._ready_for_realtime_input = True

    async def _create_initial_greeting_response(self, greeting_text: str):
        """Ask Gemini Live to speak the configured greeting exactly once."""
        if self._disconnecting:
            return

        if not self._session:
            self._pending_initial_greeting_text = greeting_text
            self._run_llm_when_session_ready = True
            return

        self._pending_initial_greeting_text = None
        prompt = format_static_greeting_prompt(greeting_text)
        turn = Content(role="user", parts=[Part(text=prompt)])

        logger.debug("Creating Gemini Live initial response from static greeting")

        await self.start_ttfb_metrics()

        try:
            await self._session.send_client_content(
                turns=[turn],
                turn_complete=True,
            )
            # Gemini 3.x also needs a realtime-input nudge to begin inference.
            if self._is_gemini_3:
                await self._session.send_realtime_input(text=" ")
        except Exception as e:
            await self._handle_send_error(e)

        self._ready_for_realtime_input = True

    # ------------------------------------------------------------------
    # Session lifecycle: suppress upstream's automatic initial-context seed,
    # because Dograh's TTSSpeakFrame is the explicit first-turn trigger. Keep
    # upstream's no-handle reconnect seed so transient failures retain history;
    # intentional node transitions still wait for their updated context frame.
    # ------------------------------------------------------------------

    @traced_gemini_live(operation="llm_setup")
    async def _handle_session_ready(self, session):
        logger.debug(
            f"In _handle_session_ready self._run_llm_when_session_ready: {self._run_llm_when_session_ready}"
        )
        self._session = session
        if self._awaiting_node_transition_context:
            # Do not accept realtime input until the function-call result frame
            # has updated the shared context and that complete history is seeded.
            self._ready_for_realtime_input = False
            await self._maybe_seed_node_transition_context()
            return

        reconnecting_after_error = self._reconnecting_after_error
        self._reconnecting_after_error = False
        if self._run_llm_when_session_ready:
            # Context arrived before session was ready — fulfil the queued
            # initial response now.
            self._run_llm_when_session_ready = False
            if self._pending_prerecorded_greeting is not None:
                # This is a brand-new session, so no queued result can refer to
                # a function call issued by it.
                self._pending_tool_results.clear()
                (transcript,) = self._pending_prerecorded_greeting
                await self._create_prerecorded_greeting_response(transcript)
            elif self._pending_initial_greeting_text is not None:
                self._pending_tool_results.clear()
                await self._create_initial_greeting_response(
                    self._pending_initial_greeting_text
                )
            else:
                await self._prepare_context_for_fresh_session()
                await self._create_initial_response()
        elif self._session_resumption_handle:
            # The provider restores the session; pending tool results can now
            # be replayed without re-sending local history.
            self._ready_for_realtime_input = True
            await self._drain_pending_tool_results()
        elif reconnecting_after_error and self._context:
            # Pipecat 1.8.1 added this recovery for failures that happen before
            # Gemini sends a resumption handle. Represent completed tool calls
            # in the history seed; the fresh session must not receive their old
            # IDs again through the live tool-response channel.
            await self._prepare_context_for_fresh_session()
            await self._create_initial_response(for_reconnect=True)
        # Otherwise this is Dograh's initial, pre-populated connection. Wait
        # for its TTSSpeakFrame/context trigger instead of auto-generating.

    async def _maybe_seed_node_transition_context(self) -> None:
        if (
            not self._awaiting_node_transition_context
            or not self._node_transition_context_received
            or not self._session
            or self._node_transition_context_seed_started
        ):
            return

        self._node_transition_context_seed_started = True
        try:
            await self._prepare_context_for_fresh_session()
            await self._create_initial_response()
            self._awaiting_node_transition_context = False
            self._node_transition_context_received = False
        finally:
            self._node_transition_context_seed_started = False
