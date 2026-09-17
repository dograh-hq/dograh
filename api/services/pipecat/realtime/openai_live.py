"""OpenAI Live with Dograh workflow tools and conversation controls."""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from loguru import logger

from api.services.pipecat.realtime.conversation import RealtimeConversationMixin
from api.services.pipecat.usage_metrics import LiveUsageMetricsData
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
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
from pipecat.services.openai.responses.llm import (
    OpenAIResponsesLLMService,
    OpenAIResponsesReasoningConfig,
)
from pipecat.utils.types import NOT_GIVEN, is_given

VOICE_INSTRUCTIONS = """You are the spoken interface of a workflow assistant.
Speak naturally and briefly, in the caller's language. Stay in the language
established with the caller for the whole call; switch languages only if the
caller explicitly asks to switch. Let the caller finish.
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

# Workflow-transition policy: the tool call is the mechanism that actually
# moves the workflow to the next node. The backend must not drift into the
# next node's conversation without invoking its transition tool first, but it
# must also not transition prematurely: complete the current node's purpose,
# check the transition conditions, and only then call. Generic over all
# workflows: no node names, edge labels, or business rules are hardcoded here.
TRANSITION_POLICY = """Workflow transition policy: a transition tool call is what
actually moves the workflow to the next node. Complete the current node's
purpose first, then determine whether a transition condition is satisfied.
When the condition for a workflow transition is satisfied, you MUST call the
corresponding transition tool; do not merely acknowledge the condition and
conversationally start asking questions that belong to the next node. Do not
continue as though the workflow has advanced until the transition tool has
been executed. On a node with several transition tools, select the one whose
condition matches the caller's situation. On a node with a single transition
tool, call it once the current node's required interaction is complete. Do
not call a transition merely because the caller mentioned something related
to the next node while the current node still needs information. After a
transition tool call, the workflow engine provides the next node's
instructions; do not simulate the next node before the transition occurs.
Do not speak closing, summary-of-completion, or goodbye content while the
current node is not terminal; closing content does not end the call — only
a transition or end-call tool does.

"""

# Initial-turn guard: prepended to the backend instructions while the caller
# has not spoken yet. Worded as a conditional so it self-expires once the
# first caller utterance arrives — no removal update needed afterwards.
# Mechanical enforcement (withheld transition tools, see _delegation_config)
# backs this wording; neither relies on the other alone.
INITIAL_TURN_RULE = """Initial-turn rule: this is the start of the call and the
caller has not spoken yet. Deliver the current node's opening instruction
first, then wait for the caller to speak. Do not call workflow transition
tools before the caller has spoken.

"""

# Backend liveness policy: consecutive tool-less backend responses (with no
# caller input between them) before the delegation constrains tool choice.
# Rationale: Dograh transitions are advisory LLM tools, so "mandatory" cannot
# come from workflow state alone. What IS observable in-service is a backend
# that keeps producing text without ever invoking a tool while transition
# tools are advertised. Past this threshold the backend is monologuing rather
# than progressing, so the delegation escalates:
# auto -> named single transition | "required" among several | auto (none).
# Reset on caller transcript, observed function call, and node change.
# Never armed during the initial-turn guard, and never counted before the
# first caller input (caller silence must not force tool calls).
TOOL_CHOICE_ESCALATION_THRESHOLD = 3


class DograhOpenAILiveLLMService(RealtimeConversationMixin, OpenAILiveLLMService):
    """Keep workflow instructions on the Responses backend of a Live session."""

    def __init__(
        self,
        *,
        backend_model: str,
        reasoning_effort: str = "low",
        web_search: bool = False,
        language: str | None = None,
        settings=None,
        **kwargs,
    ):
        settings = settings or self.Settings()
        voice_instructions = VOICE_INSTRUCTIONS
        if language:
            voice_instructions += (
                f"\nConversation language: converse in {language} (ISO 639-1) "
                "unless the caller explicitly asks to switch languages."
            )
        super().__init__(
            settings=replace(settings, system_instruction=voice_instructions),
            delegation=self.ResponsesDelegation(
                settings=OpenAIResponsesLLMService.Settings(
                    model=backend_model,
                    reasoning=OpenAIResponsesReasoningConfig(effort=reasoning_effort),
                )
            ),
            **kwargs,
        )
        self._web_search_enabled = web_search
        self._language = language
        # Backend liveness tracking for the tool_choice escalation policy.
        self._backend_responses_without_calls = 0
        self._response_had_calls = False
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
        # Initial-turn guard: while True, node-transition tools are withheld
        # from the Responses delegation and the backend instructions carry
        # INITIAL_TURN_RULE. Released on the first caller transcript so the
        # backend cannot transition before the caller has spoken.
        self._awaiting_caller_input = True
        # Terminal teardown: at most one session.close per session lifetime.
        self._close_requested = False
        # Teardown commitment: once cancel()/stop() has committed terminal
        # teardown for this service lifecycle, a stale or delayed
        # session.started event must never resurrect it. Reconnects flow
        # through _disconnect (transient) and never set this flag.
        self._teardown_committed = False
        # Staleness currency: bumped on every workflow node change. Backend
        # function calls are tagged with the revision observed when their
        # output item arrived; execution requires a current revision, a still
        # advertised function, and a non-terminal session.
        self._node_revision = 0
        self._call_revisions: dict = {}
        self._terminal = False

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

    async def _handle_evt_error(self, evt):
        await super()._handle_evt_error(evt)

    async def _handle_evt_delegation_created(self, evt):
        await super()._handle_evt_delegation_created(evt)

    @staticmethod
    def _response_inner(evt):
        inner = getattr(evt, "event", None)
        if isinstance(inner, Mapping):
            return inner.get("type", "dict"), inner.get("item") or {}
        if inner is None:
            return None, None
        return (
            getattr(inner, "type", type(inner).__name__),
            getattr(inner, "item", None),
        )

    @staticmethod
    def _is_function_call_item(item) -> bool:
        if isinstance(item, Mapping):
            return item.get("type") == "function_call"
        return getattr(item, "type", None) == "function_call"

    async def _handle_evt_response(self, evt):
        itype, item = self._response_inner(evt)
        if itype == "response.created":
            self._response_had_calls = False
        elif itype == "response.output_item.done" and self._is_function_call_item(item):
            # Backend is acting through tools: reset the liveness counter and
            # tag the call with the current node revision for the staleness
            # check at execution time.
            self._response_had_calls = True
            self._backend_responses_without_calls = 0
            call_id = (
                item.get("call_id")
                if isinstance(item, Mapping)
                else getattr(item, "call_id", None)
            )
            if isinstance(call_id, str) and call_id:
                self._call_revisions[call_id] = self._node_revision
        elif itype == "response.completed" and not self._awaiting_caller_input:
            if self._response_had_calls:
                self._backend_responses_without_calls = 0
            else:
                self._backend_responses_without_calls += 1
            # Propagates an escalation boundary (if reached) via session.update;
            # otherwise a snapshot no-op.
            await self._maybe_send_tools_update()
        await super()._handle_evt_response(evt)

    async def _handle_evt_session_closed(self, evt):
        await super()._handle_evt_session_closed(evt)

    async def _handle_evt_transcript_delta(self, evt):
        await super()._handle_evt_transcript_delta(evt)
        # Any caller utterance resets the liveness counter: while the caller
        # is providing input, gathering is progressing and no escalation may
        # fire. The first utterance additionally releases the initial-turn
        # guard and publishes the full tool set.
        if getattr(evt, "role", None) == "user" and getattr(evt, "delta", None):
            self._backend_responses_without_calls = 0
            if self._awaiting_caller_input:
                self._awaiting_caller_input = False
            await self._maybe_send_tools_update()

    def _initial_turn_tools(self, tools):
        # Node-transition tools are withheld until the caller has spoken;
        # everything else (ordinary tools, web_search entries without a
        # function name) passes through untouched.
        if not self._awaiting_caller_input or not tools:
            return tools
        return [
            tool
            for tool in tools
            if not (
                isinstance(tool, dict)
                and isinstance(tool.get("name"), str)
                and self._function_is_node_transition(tool["name"])
            )
        ]

    def _backend_tools(self, tools):
        # Context tools filtered by the initial-turn guard, plus the opt-in
        # Responses web_search tool. Single source for session.start and
        # session.update so the two can never disagree.
        tools = self._initial_turn_tools(tools)
        if self._web_search_enabled and isinstance(tools, list):
            tools = [*tools, {"type": "web_search"}]
        return tools

    def _transition_tool_names(self, tools) -> list:
        # Advertised node-transition tool names, in order, de-duplicated.
        names = []
        for tool in tools or []:
            if (
                isinstance(tool, dict)
                and isinstance(tool.get("name"), str)
                and self._function_is_node_transition(tool["name"])
                and tool["name"] not in names
            ):
                names.append(tool["name"])
        return names

    def _escalated_tool_choice(self, tools):
        # State-aware tool_choice policy for the Responses backend:
        # - initial-turn guard armed (opening turn) -> auto (None);
        # - no transition tools advertised (e.g. terminal nodes) -> auto;
        # - below monologue threshold -> auto;
        # - exactly one valid transition at/above threshold -> force it by name;
        # - several valid transitions at/above threshold -> require a tool call.
        # "Mandatory" is approximated as sustained tool-less backend output
        # while transitions are advertised — a liveness guard, not a semantic
        # oracle. Ordinary tools stay advertised throughout; "required" may
        # still select one, which is correct Responses behavior.
        if self._awaiting_caller_input:
            return None
        names = self._transition_tool_names(tools)
        if self._backend_responses_without_calls < TOOL_CHOICE_ESCALATION_THRESHOLD:
            return None
        if len(names) == 1:
            return {"type": "function", "name": names[0]}
        if len(names) > 1:
            return "required"
        return None

    def _delegation_config(self, tools, tool_choice):
        # Initial-turn guard (mechanical half): withhold node-transition tools
        # from the Responses delegation until the caller has spoken. Applies to
        # session.start and session.update uniformly since both build through
        # here. Non-transition tools pass through untouched.
        # Liveness escalation overrides tool_choice as documented above.
        # When not escalated, send explicit "auto": the Live API retains
        # omitted settings server-side, so a previously sent "required"/named
        # choice would otherwise survive de-escalation and later combine with
        # an empty tool list (e.g. terminal nodes), which the API rejects.
        # "auto" is the documented default, so this is a no-op semantically
        # except for clearing stale server-side state.
        escalated = self._escalated_tool_choice(tools)
        return super()._delegation_config(
            self._backend_tools(tools),
            escalated if escalated is not None else "auto",
        )

    async def _update_settings(self, delta):
        # The voice prompt is fixed for the session; node prompts and tools
        # belong to the backend and can change without losing the conversation.
        if is_given(delta.system_instruction):
            node_prompt = delta.system_instruction or ""
            if self._awaiting_caller_input:
                node_prompt = INITIAL_TURN_RULE + node_prompt
            if self._language:
                node_prompt += (
                    f"\nConversation language: conduct this workflow in "
                    f"{self._language} (ISO 639-1) unless the caller explicitly "
                    "asks to switch languages."
                )
            new_instructions = BACKEND_INSTRUCTIONS + TRANSITION_POLICY + node_prompt
            current = self._delegation.settings.system_instruction
            self._delegation.settings.system_instruction = new_instructions
            # New node instructions mean a new workflow position: advance the
            # staleness currency so backend work from the previous node can be
            # recognized (and dropped) at execution time. Also a fresh slate
            # for the liveness policy. Same-content re-sets don't advance.
            if not is_given(current) or current != new_instructions:
                self._node_revision += 1
            self._backend_responses_without_calls = 0
            self._response_had_calls = False
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
        # Route through the backend tool builder so a tools update can never
        # re-publish withheld transition tools while the guard is armed, and
        # the web_search opt-in stays in sync with session.start.
        delegation.responses["tools"] = self._backend_tools(params["tools"]) or []
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

    async def stop(self, frame):
        # EndFrame means the call is ending: mark terminal so late backend
        # events cannot resurrect workflow activity, then run the graceful
        # close handshake.
        self._terminal = True
        self._teardown_committed = True
        await super().stop(frame)

    async def _handle_evt_session_started(self, evt):
        if self._teardown_committed:
            # Stale or delayed start arriving after teardown committed for
            # this service lifecycle: skip upstream bookkeeping, opening
            # speech, and the terminal reset so a dead session cannot resume
            # speaking or executing workflow activity.
            return
        self._terminal = False
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

    async def _send_context_append(self, delegation_id, text, *, spoken):
        # No new context appends once termination has begun: late backend
        # events cannot resurrect workflow activity.
        if self._terminal:
            return
        await super()._send_context_append(delegation_id, text, spoken=spoken)

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
        if not text.strip() or self._terminal:
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
                # Re-enter through the gated path: the node may have changed
                # (or the session terminated) while these waited.
                await self.run_function_calls(calls)
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

    def _is_currently_advertised(self, function_name: str) -> bool:
        # A transition is only valid while the active node still advertises
        # it. The engine rewrites context tools on every set_node, so the
        # current context is the authority — not the handler registry, which
        # may still hold handlers from previous nodes. Fail open only when
        # there is genuinely no tool metadata to consult (no context at
        # all); an explicit empty list means the node advertises nothing and
        # must fail closed so stale calls cannot execute.
        context = self._context
        tools = getattr(context, "tools", None) if context is not None else None
        standard = getattr(tools, "standard_tools", None) if tools is not None else None
        if standard is None:
            return True
        for tool in standard:
            name = (
                tool.get("name")
                if isinstance(tool, dict)
                else getattr(tool, "name", None)
            )
            if name == function_name:
                return True
        return False

    async def _maybe_continue_response(self, key):
        # No new backend delegations once termination has begun.
        if self._terminal:
            return
        await super()._maybe_continue_response(key)

    async def run_function_calls(self, function_calls: Sequence[FunctionCallFromLLM]):
        # Terminal gate + staleness gate before any execution or deferral.
        live_calls = []
        for call in function_calls:
            if self._terminal:
                continue
            known_revision = self._call_revisions.pop(call.tool_call_id, None)
            if known_revision is not None and known_revision != self._node_revision:
                continue
            if not self._is_currently_advertised(call.function_name):
                continue
            live_calls.append(call)
        if not live_calls:
            return
        # Keep a batch intact so a transition cannot outrun related tool calls.
        if self._bot_is_speaking and any(
            self._function_is_node_transition(call.function_name) for call in live_calls
        ):
            self._deferred_transitions.extend(live_calls)
            return
        await super().run_function_calls(live_calls)

    async def _disconnect(self):
        self._bot_is_speaking = False
        self._deferred_transitions.clear()
        self._pending_speech.clear()
        self._initial_backend_request = False
        self._sent_backend_snapshot = None
        self._call_revisions.clear()
        self._close_requested = False
        await super()._disconnect()

    async def cancel(self, frame: CancelFrame):
        # Terminal states (end_call tool, hangup, dispose) arrive as
        # CancelFrame, whose upstream handler only closes the websocket.
        # Send session.close best-effort first so the Live session actually
        # terminates server-side instead of lingering until timeout. No waiting:
        # CancelFrame means teardown is already in progress downstream.
        # From here on the session is terminal: late backend work is dropped.
        self._terminal = True
        self._teardown_committed = True
        if (
            self._websocket
            and self._session_started
            and not self._disconnecting
            and not self._close_requested
        ):
            try:
                self._close_requested = True
                await self.send_client_event(events.SessionCloseEvent())
            except Exception as exc:  # noqa: BLE001 - best effort on teardown path
                logger.debug(f"{self}: session.close send failed during cancel: {exc}")
        await super().cancel(frame)
