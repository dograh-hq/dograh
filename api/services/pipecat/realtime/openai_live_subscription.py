"""Experimental subscription voice with Dograh-owned workflow delegation."""

import asyncio
from collections.abc import Sequence
from contextvars import ContextVar
from dataclasses import dataclass, replace
from time import monotonic
from uuid import uuid4

from loguru import logger

from api.services.pipecat.realtime.conversation import RealtimeConversationMixin
from api.services.pipecat.realtime.openai_live import (
    BACKEND_INSTRUCTIONS,
    VOICE_INSTRUCTIONS,
)
from api.services.pipecat.realtime.openai_live_subscription_transport import (
    OpenAILiveSubscriptionTransport,
    build_subscription_session,
    context_append_events,
    delegation_context_events,
)
from api.services.pipecat.realtime.openai_subscription_llm import (
    SubscriptionResponsesLLMService,
)
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    FunctionCallCancelFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    FunctionCallResultProperties,
    FunctionCallsStartedFrame,
    InputAudioRawFrame,
    LLMMessagesAppendFrame,
    LLMSetToolsFrame,
    MetricsFrame,
    NodeTransitionStartedFrame,
    SpeechOutputAudioRawFrame,
    UserMuteStartedFrame,
    UserMuteStoppedFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import FunctionCallFromLLM
from pipecat.services.openai.live import events
from pipecat.services.openai.live.llm import ClientDelegation, OpenAILiveLLMService
from pipecat.services.settings import LLMSettings
from pipecat.utils.types import NOT_GIVEN, assert_given, is_given
from pipecat.workers.llm.backend_llm_worker import (
    BackendLLMWorker,
    _delegate_to_backend,
    _render_transcript_request,
)


@dataclass
class _Delegation:
    id: str
    session: str
    node_revision: int
    input_revision: int
    request: str
    provider_id: str | None = None


class _WorkflowBackend(SubscriptionResponsesLLMService):
    def __init__(self, *, owner, **kwargs):
        self.owner = owner
        super().__init__(**kwargs)

    async def run_function_calls(self, function_calls: Sequence[FunctionCallFromLLM]):
        if any(
            self._function_is_node_transition(c.function_name) for c in function_calls
        ):
            await self.owner._playback_idle.wait()
        if not self.owner._is_current(self.owner._active_delegation):
            for call in function_calls:
                await self.broadcast_frame(
                    FunctionCallCancelFrame,
                    function_name=call.function_name,
                    tool_call_id=call.tool_call_id,
                )
            return
        await super().run_function_calls(function_calls)

    async def _run_function_call(self, item):
        # A queued batch can outlive a caller correction or a node replacement.
        if not self.owner._is_current(
            self.owner._active_delegation
        ) or item.function_name not in self._advertised_tool_names(item.context):
            item.settled = True
            await self.broadcast_frame(
                FunctionCallCancelFrame,
                function_name=item.function_name,
                tool_call_id=item.tool_call_id,
            )
            return
        token = None
        if self._function_is_node_transition(item.function_name):
            token = self.owner._transition_origin.set(self.owner._active_delegation)
        try:
            await super()._run_function_call(item)
        finally:
            if token is not None:
                self.owner._transition_origin.reset(token)

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        if isinstance(
            frame,
            (
                FunctionCallsStartedFrame,
                FunctionCallInProgressFrame,
                FunctionCallResultFrame,
                FunctionCallCancelFrame,
                NodeTransitionStartedFrame,
                MetricsFrame,
            ),
        ):
            mirrored = replace(frame)
            if isinstance(mirrored, FunctionCallResultFrame):
                # Only the backend aggregator runs inference and the workflow's
                # context-updated callback; the voice context records the result.
                mirrored.run_llm = False
                mirrored.properties = FunctionCallResultProperties(
                    run_llm=False,
                    is_final=frame.properties.is_final if frame.properties else True,
                )
            if isinstance(mirrored, FunctionCallCancelFrame):
                mirrored.run_llm = False
            await self.owner.push_frame(mirrored, direction)
        await super().push_frame(frame, direction)


class DograhOpenAILiveSubscriptionLLMService(
    RealtimeConversationMixin, OpenAILiveLLMService
):
    """Use the pinned ClientDelegation worker API with subscription WebRTC media."""

    FAILURE_SPEECH_START_TIMEOUT = 5.0
    FAILURE_SPEECH_PLAYBACK_TIMEOUT = 30.0

    def __init__(
        self,
        *,
        backend_model: str,
        auth_service,
        organization_id: int,
        settings=None,
        transport_factory=OpenAILiveSubscriptionTransport,
        delegation_timeout_secs: float = 60.0,
        **kwargs,
    ):
        if not backend_model:
            raise ValueError("A subscription workflow backend model is required")
        auth_service.assert_organization(organization_id)
        self._auth_service = auth_service
        self._organization_id = organization_id
        self._lease = None
        self._lease_task = None
        self._session_identity = uuid4().hex
        self._node_revision = 0
        self._input_revision = 0
        self._seen_delegations: set[str] = set()
        self._seen_events: set[str] = set()
        self._transcript_items: dict[str, str] = {}
        self._active_delegation: _Delegation | None = None
        self._backend_lock = asyncio.Lock()
        self._playback_idle = asyncio.Event()
        self._playback_idle.set()
        self._failure_speech_started = asyncio.Event()
        self._failure_speech_finished = asyncio.Event()
        self._backend_failure_pending = False
        self._transition_origin = ContextVar("subscription_transition", default=None)
        self._pending_speech: list[str] = []
        self._initial_backend_request = False
        self._terminal = False
        self._disconnect_task = None
        self._terminal_event_task = None
        self._disconnect_waiters: set[asyncio.Task] = set()
        self._started_at = None
        self._usage_reported = False
        self._input_audio_seconds = 0.0
        self._output_audio_seconds = 0.0
        self._session_state = "ended"
        self._connecting = False
        self._backend_context = LLMContext()
        self._backend_llm = _WorkflowBackend(
            owner=self,
            auth_service=auth_service,
            organization_id=organization_id,
            settings=SubscriptionResponsesLLMService.Settings(model=backend_model),
        )
        self._backend_worker = BackendLLMWorker(
            llm=self._backend_llm,
            context=self._backend_context,
        )
        settings = settings or self.Settings(model="gpt-live-1-codex", voice="cove")
        super().__init__(
            api_key="",
            settings=replace(settings, system_instruction=VOICE_INSTRUCTIONS),
            delegation=ClientDelegation(
                backend=self._backend_worker, timeout_secs=delegation_timeout_secs
            ),
            **kwargs,
        )
        self._transport = transport_factory(
            on_event=self._on_subscription_event,
            on_audio=self._on_subscription_audio,
        )

    @property
    def inference_llm(self):
        return self._backend_llm

    @property
    def backend_model(self):
        return assert_given(self._backend_llm._settings.model)

    def register_function(self, function_name, handler, **kwargs):
        super().register_function(function_name, handler, **kwargs)
        self._backend_llm.register_function(function_name, handler, **kwargs)
        # The outer service owns handler resources; do not run cleanup twice.
        self._backend_llm._tool_cleanups.clear()

    def unregister_function(self, function_name):
        super().unregister_function(function_name)
        if self._backend_llm.has_function(function_name):
            self._backend_llm.unregister_function(function_name)

    async def _update_settings(self, delta):
        if is_given(delta.system_instruction):
            self._node_revision += 1
            originating = self._transition_origin.get()
            if originating is not None and originating is self._active_delegation:
                originating.node_revision = self._node_revision
            await self._backend_llm._update_settings(
                LLMSettings(
                    system_instruction=BACKEND_INSTRUCTIONS
                    + (delta.system_instruction or "")
                )
            )
        changed = await super()._update_settings(
            replace(delta, system_instruction=NOT_GIVEN)
        )
        await self._maybe_send_tools_update()
        return changed

    async def _maybe_send_tools_update(self):
        if self._context is None:
            return
        self._sync_registered_tool_handlers(self._context.tools)
        self._backend_context.set_tools(self._context.tools)
        self._backend_context.set_tool_choice(self._context.tool_choice)
        self._backend_llm._sync_registered_tool_handlers(self._context.tools)
        self._backend_llm._tool_cleanups.clear()

    async def _connect(self):
        if self._lease is None and not self._terminal:
            try:
                self._lease = await self._auth_service.acquire_session(
                    self._organization_id
                )
                self._backend_llm.bind_account(self._lease.credentials.account_id)
                self._lease_task = self.create_task(
                    self._renew_lease(), "subscription-lease"
                )
            except Exception:  # noqa: BLE001 - provider boundary, redact secrets
                await self._fail(
                    "Subscription voice login is unavailable or already in use."
                )

    async def _renew_lease(self):
        try:
            while self._lease is not None:
                await asyncio.sleep(30)
                await self._lease.renew()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - redact provider and credential details
            await self._fail(
                "Subscription voice account lease was lost. Start a new conversation."
            )

    async def _send_session_config(self):
        if self._terminal or self._connecting or not self._needs_session_config:
            return
        self._connecting = True
        try:
            await self._connect()
            if self._lease is None or self._terminal:
                return
            self._needs_session_config = False
            session = build_subscription_session(
                model=self._session_model,
                voice=(
                    self._settings.voice if is_given(self._settings.voice) else "cove"
                ),
                instructions=VOICE_INSTRUCTIONS,
            )
            credentials = self._lease.credentials
            await self._transport.connect(
                access_token=credentials.access_token,
                account_id=credentials.account_id,
                session=session,
            )
            if not self._terminal:
                await self._handle_evt_session_started(
                    events.SessionStartedEvent(
                        type="session.started",
                        session=events.SessionResource(model=self._session_model),
                    )
                )
        except Exception:  # noqa: BLE001 - redact provider and credential details
            await self._fail(
                "Subscription voice connection failed. Check login and account access."
            )
        finally:
            self._connecting = False

    async def _handle_context(self, context):
        if context is None:
            return
        self._handled_initial_context = True
        self._context = context
        await self._maybe_send_tools_update()
        if self._needs_session_config:
            self._initial_backend_request = not self._pending_speech
            # Seed only transcript text: backend tool history is owned by its worker.
            for message in context.messages:
                if isinstance(message, dict) and message.get("role") in {
                    "user",
                    "assistant",
                }:
                    text = message.get("content")
                    if isinstance(text, str) and text:
                        self._remember_fragment(message["role"], text)
            await self._send_session_config()

    async def _handle_evt_session_started(self, evt):
        if self._session_started or self._terminal:
            return
        self._started_at = monotonic()
        await super()._handle_evt_session_started(evt)
        pending, self._pending_speech = self._pending_speech, []
        for text in pending:
            await self._send_speech_instruction(text)
        if self._initial_backend_request and not pending:
            await self._handle_client_delegation(
                events.DelegationMetadata(
                    id=f"opening-{self._session_identity}",
                    target="client",
                )
            )
        self._initial_backend_request = False

    async def _send_context_append(self, delegation_id, text, *, spoken):
        if self._terminal or not self._session_started:
            return
        wire_events = (
            delegation_context_events(delegation_id, text, spoken=spoken)
            if delegation_id is not None
            else context_append_events(
                text, kind="commentary" if spoken else "thinking"
            )
        )
        for event in wire_events:
            await self._transport.send_event(event)

    async def _send_speech_instruction(self, text):
        for event in context_append_events(text, kind="instructions"):
            await self._transport.send_event(event)

    async def _speak(self, text):
        if not text.strip():
            return
        if self._session_started:
            await self._send_speech_instruction(text)
        else:
            self._pending_speech.append(text)
            self._initial_backend_request = False
            if self._context is not None:
                await self._handle_context(self._context)

    async def _handle_initial_greeting(self, context, greeting_text):
        if context is None:
            return
        self._handled_initial_context = True
        self._context = context
        await self._speak(
            "Speak immediately, without waiting for the caller. Say the following "
            "text aloud in its original language, then wait. Do not add a preamble:\n"
            + greeting_text
        )

    async def _send_user_audio(self, frame: InputAudioRawFrame):
        if not self._session_started or self._terminal:
            return
        # The transport masks again after its resampler to prevent buffered speech leaking.
        frame = await self._prepare_audio_frame(frame)
        await self._transport.send_audio(
            frame.audio, frame.sample_rate, frame.num_channels
        )
        self._input_audio_seconds += len(frame.audio) / (
            2 * frame.sample_rate * frame.num_channels
        )

    async def _on_subscription_audio(self, pcm, sample_rate, channels):
        if not self._terminal and self._session_started:
            self._output_audio_seconds += len(pcm) / (2 * sample_rate * channels)
            await self.push_frame(
                SpeechOutputAudioRawFrame(
                    audio=pcm,
                    sample_rate=sample_rate,
                    num_channels=channels,
                )
            )

    async def process_frame(self, frame, direction):
        if isinstance(frame, LLMMessagesAppendFrame):
            for message in frame.messages:
                text = message.get("content") if isinstance(message, dict) else None
                if isinstance(text, str) and text.strip():
                    if frame.run_llm:
                        await self._speak(text)
                    else:
                        await self._send_context_append(None, text, spoken=False)
            return
        if isinstance(frame, BotStartedSpeakingFrame):
            self._playback_idle.clear()
            if self._backend_failure_pending:
                self._failure_speech_started.set()
                self._failure_speech_finished.clear()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._playback_idle.set()
            if self._failure_speech_started.is_set():
                self._failure_speech_finished.set()
        elif isinstance(frame, (UserMuteStartedFrame, UserMuteStoppedFrame)):
            self._transport.set_muted(isinstance(frame, UserMuteStartedFrame))
        elif isinstance(frame, LLMSetToolsFrame) and self._context is not None:
            self._context.set_tools(frame.tools)
        await super().process_frame(frame, direction)

    def _is_current(self, request):
        return (
            request is not None
            and not self._terminal
            and not self._backend_failure_pending
            and self._session_started
            and request.session == self._session_identity
            and request.node_revision == self._node_revision
            and request.input_revision == self._input_revision
        )

    async def _handle_client_delegation(self, delegation):
        if (
            delegation.id in self._seen_delegations
            or self._terminal
            or self._backend_failure_pending
        ):
            return
        self._seen_delegations.add(delegation.id)
        content = getattr(delegation, "content", None) or []
        instruction = (
            "\n".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
            or "Act on the user's most recent request. Use the current workflow and tools."
        )
        request = _Delegation(
            id=delegation.id,
            session=self._session_identity,
            node_revision=self._node_revision,
            input_revision=self._input_revision,
            provider_id=delegation.id,
            request=instruction,
        )
        if delegation.id == f"opening-{self._session_identity}":
            request.provider_id = None
        task = self.create_task(
            self._run_subscription_delegation(request),
            f"subscription-delegation:{delegation.id}",
        )
        self._delegation_tasks[delegation.id] = task
        task.add_done_callback(
            lambda _: self._delegation_tasks.pop(delegation.id, None)
        )

    async def _run_subscription_delegation(self, request):
        async with self._backend_lock:
            if not self._is_current(request):
                return
            request.request = _render_transcript_request(
                self._take_transcript(),
                instruction=request.request,
                first=not self._delegated_before,
            )
            self._delegated_before = True
            self._active_delegation = request
            answered = False

            async def on_update(output):
                nonlocal answered
                if not self._is_current(request):
                    return
                if output.is_final and output.prefers_spoken:
                    answered = True
                await self._send_context_append(
                    request.provider_id,
                    output.text,
                    spoken=output.prefers_spoken and not output.is_thought,
                )

            try:
                # Final text is also returned by the worker. Consume updates only.
                await _delegate_to_backend(
                    self.pipeline_worker,
                    self._backend_worker.name,
                    request=request.request,
                    on_update=on_update,
                    timeout_secs=self._delegation.timeout_secs,
                )
                if self._is_current(request) and not answered:
                    await self._finish_backend_failure(
                        request,
                        "The workflow finished without an answer. Please try a new conversation.",
                        "Workflow backend produced no final answer.",
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - provider boundary, redact secrets
                if self._is_current(request):
                    await self._finish_backend_failure(
                        request,
                        "I could not complete that request. I cannot confirm that any action succeeded.",
                        "Workflow backend failed. Start a new conversation.",
                    )
            finally:
                if self._active_delegation is request:
                    self._active_delegation = None

    async def _finish_backend_failure(self, request, speech, error):
        if self._backend_failure_pending or self._terminal:
            return
        self._backend_failure_pending = True
        try:
            # Pipecat emits no second start during continuous playback. Wait
            # for an existing utterance to drain before arming a new one.
            if not self._playback_idle.is_set():
                await asyncio.wait_for(
                    self._playback_idle.wait(),
                    timeout=self.FAILURE_SPEECH_PLAYBACK_TIMEOUT,
                )
            self._failure_speech_started.clear()
            self._failure_speech_finished.clear()
            await self._send_context_append(request.provider_id, speech, spoken=True)
            # Match PipecatEngine.wait_for_speech_playback: require a fresh
            # playback start followed by stop, with separate bounded deadlines.
            await asyncio.wait_for(
                self._failure_speech_started.wait(),
                timeout=self.FAILURE_SPEECH_START_TIMEOUT,
            )
            await asyncio.wait_for(
                self._failure_speech_finished.wait(),
                timeout=self.FAILURE_SPEECH_PLAYBACK_TIMEOUT,
            )
        except TimeoutError:
            logger.warning("Subscription backend failure playback deadline elapsed")
        except Exception:  # noqa: BLE001 - provider failure still requires cleanup
            logger.warning(
                "Subscription backend failure announcement could not be delivered"
            )
        finally:
            await self._fail(error)

    async def _on_subscription_event(self, event):
        if self._terminal or self._terminal_event_task is not None:
            return
        event_id = event.get("event_id")
        if event_id:
            if event_id in self._seen_events:
                return
            self._seen_events.add(event_id)
        kind = event.get("type", "")
        if kind in {"error", "session.error", "session.closed"}:
            if kind == "session.closed":
                self._session_closed_event.set()
            # A transport receive task cannot await cleanup that cancels and
            # joins that same task. Hand terminal reporting to an owned task.
            self._terminal_event_task = asyncio.create_task(
                self._fail(
                    "Subscription voice became unavailable. Start a new conversation."
                )
            )
        elif kind in {"session.started", "session.created"}:
            await self._handle_evt_session_started(
                events.SessionStartedEvent(
                    type="session.started",
                    session=events.SessionResource(**event.get("session", {})),
                )
            )
        elif kind in {"session.delegation.created", "delegation.created"}:
            item = event.get("delegation") or event.get("item") or {}
            if item.get("target") == "client" and isinstance(item.get("id"), str):
                await self._handle_client_delegation(events.DelegationMetadata(**item))
        elif kind in {
            "session.input_transcript.delta",
            "session.output_transcript.delta",
        }:
            role = "user" if "input" in kind else "assistant"
            delta = event.get("delta", "")
            await self._accept_transcript(role, delta)
            if isinstance(delta, str):
                item_id = event.get("item_id") or role
                self._transcript_items[item_id] = (
                    self._transcript_items.get(item_id, "") + delta
                )
        elif kind == "turn.done" or kind.endswith("_transcript.added"):
            item = event.get("turn") or event.get("item") or {}
            if not isinstance(item, dict):
                return
            role = (
                item.get("role")
                if kind == "turn.done"
                else ("user" if "input" in kind else "assistant")
            )
            text = item.get("transcript") if kind == "turn.done" else item.get("text")
            if role not in {"user", "assistant"} or not isinstance(text, str):
                return
            turn = self._user_turn if role == "user" else self._assistant_turn
            item_id = item.get("id") or event.get("turn_id") or role
            # A completed turn may name a different item than its stream deltas.
            previous = self._transcript_items.get(
                item_id, turn.text if turn.open else ""
            )
            delta = text.removeprefix(previous)
            self._transcript_items[item_id] = text
            await self._accept_transcript(role, delta)
            if kind == "turn.done":
                await self._close_turn(role)
                self._transcript_items.pop(role, None)
        # Sideband audio is deliberately ignored: WebRTC is the only media source.

    async def _accept_transcript(self, role, delta):
        if role not in {"user", "assistant"} or not isinstance(delta, str) or not delta:
            return
        if role == "user":
            self._input_revision += 1
        await self._handle_evt_transcript_delta(
            events.TranscriptDeltaEvent(
                type=(
                    "session.input_transcript.delta"
                    if role == "user"
                    else "session.output_transcript.delta"
                ),
                delta=delta,
            )
        )

    async def _restart_turn_timer(self, role, turn):
        # Subscription transcripts carry explicit finality in turn.done.
        return

    async def send_client_event(self, event):
        # Never let the inherited API WebSocket transport receive OAuth credentials.
        if not self._terminal:
            await self._transport.send_event(event.to_payload())

    async def _close_session(self):
        # Closing the WebRTC peer ends this experimental session. Never replay it.
        await self._disconnect()

    async def _fail(self, message):
        if self._terminal:
            return
        self._session_state = "failed"
        await self._disconnect()
        await self.push_error(error_msg=message, force_treat_as_permanent=True)

    async def _disconnect(self):
        current = asyncio.current_task()
        self._disconnect_waiters.add(current)
        try:
            if self._disconnect_task is None:
                # Cleanup must survive the caller/worker task being cancelled.
                self._disconnect_task = asyncio.create_task(self._disconnect_owned())
            try:
                await asyncio.shield(self._disconnect_task)
            except asyncio.CancelledError:
                await asyncio.shield(self._disconnect_task)
                raise
        finally:
            self._disconnect_waiters.discard(current)

    async def _disconnect_owned(self):
        self._terminal = True
        self._session_identity = uuid4().hex
        self._session_started = False
        self._playback_idle.set()
        self._pending_speech.clear()
        self._initial_backend_request = False
        current = asyncio.current_task()
        tasks = [
            t
            for t in [self._lease_task, *self._delegation_tasks.values()]
            if t is not None and t is not current and t not in self._disconnect_waiters
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._lease_task = None
        self._delegation_tasks.clear()
        self._active_delegation = None
        self._transcript_fragments.clear()
        self._transcript_items.clear()
        # The pinned runner only messages root workers when a root has ended.
        # Explicitly finish our child so it cannot keep runner teardown waiting.
        if (
            self._backend_worker.started_at is not None
            and not self._backend_worker.has_finished()
        ):
            await self._backend_worker.queue_frame(CancelFrame())
        lease, self._lease = self._lease, None
        cleanup = [self._backend_llm.aclose, self._transport.close]
        if lease is not None:
            cleanup.append(lease.release)
        cleanup.extend(
            [
                self._auth_service.aclose,
                self._report_subscription_usage,
                self.stop_all_metrics,
            ]
        )
        for close in cleanup:
            try:
                await close()
            except Exception:  # noqa: BLE001 - provider boundary, redact secrets
                # One unavailable provider/Redis connection must not skip the
                # other owned resources or expose upstream exception details.
                self._session_state = "failed"

    async def _report_subscription_usage(self):
        if self._usage_reported or self._started_at is None:
            return
        from api.services.pipecat.usage_metrics import SubscriptionVoiceUsageMetricsData

        self._usage_reported = True
        await self.push_frame(
            MetricsFrame(
                data=[
                    SubscriptionVoiceUsageMetricsData(
                        processor=self.name,
                        model=self._session_model,
                        session_seconds=max(0.0, monotonic() - self._started_at),
                        input_audio_seconds=self._input_audio_seconds,
                        output_audio_seconds=self._output_audio_seconds,
                        session_state=self._session_state,
                    )
                ]
            )
        )
