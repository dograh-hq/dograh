import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    FunctionCallResultProperties,
    InputAudioRawFrame,
    LLMTextFrame,
    MetricsFrame,
    SpeechOutputAudioRawFrame,
    TTSSpeakFrame,
    UserMuteStartedFrame,
    UserMuteStoppedFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import FunctionCallFromLLM, LLMService
from pipecat.services.openai.live.llm import ClientDelegation
from pipecat.services.settings import LLMSettings
from pipecat.utils.types import is_given
from pipecat.workers.llm.backend_llm_worker import BackendLLMWorker, BackendOutput

from api.services.pipecat.realtime.openai_live_subscription import (
    DograhOpenAILiveSubscriptionLLMService,
    _Delegation,
)
from api.services.pipecat.worker_runner import (
    run_pipeline_worker,
    wait_for_pipeline_worker_started,
)


class FakeTransport:
    def __init__(self, *, on_event, on_audio):
        self.on_event, self.on_audio = on_event, on_audio
        self.connect = AsyncMock()
        self.send_event = AsyncMock()
        self.send_audio = AsyncMock()
        self.close = AsyncMock()
        self.muted = False

    def set_muted(self, value):
        self.muted = value


def make_service(*, ready=True, mocked_frames=True):
    lease = SimpleNamespace(
        credentials=SimpleNamespace(
            access_token="synthetic-oauth", account_id="synthetic-account"
        ),
        renew=AsyncMock(),
        release=AsyncMock(),
    )
    auth = SimpleNamespace(
        assert_organization=lambda org: None,
        acquire_session=AsyncMock(return_value=lease),
        aclose=AsyncMock(),
    )
    service = DograhOpenAILiveSubscriptionLLMService(
        backend_model="gpt-5.6-luna",
        auth_service=auth,
        organization_id=1,
        transport_factory=FakeTransport,
    )
    if mocked_frames:
        service.push_frame = AsyncMock()
        service.push_error = AsyncMock()
        service.create_task = lambda coro, *args: asyncio.create_task(coro)
        service.cancel_task = lambda task, **kw: cancel_task(task)
        service._call_event_handler = AsyncMock()
    if ready:
        service._session_started = True
        service._needs_session_config = False
    return service


async def cancel_task(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def tool(name):
    return FunctionSchema(name=name, description=name, properties={}, required=[])


def request(service, id="delegation_1"):
    return _Delegation(
        id,
        service._session_identity,
        service._node_revision,
        service._input_revision,
        "USER: look up the test entry",
    )


@pytest.mark.asyncio
async def test_real_worker_and_subscription_backend_ignore_ambient_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://untrusted.invalid")
    service = make_service()
    assert isinstance(service._backend_worker, BackendLLMWorker)
    assert isinstance(service._delegation, ClientDelegation)
    assert service._delegation.backend is service._backend_worker
    assert service.api_key == ""
    assert service._backend_llm._client is None
    assert service._backend_llm._api_key is None
    assert service.inference_llm is service._backend_llm
    assert service._backend_llm._settings.model == "gpt-5.6-luna"
    await service._disconnect()


@pytest.mark.asyncio
async def test_node_prompt_tools_add_remove_and_registry_forwarding():
    service = make_service()
    service._context = LLMContext(tools=ToolsSchema(standard_tools=[tool("lookup")]))
    handler = AsyncMock()
    service.register_function(
        "lookup", handler, is_node_transition=True, cancellable_by_llm=False
    )
    await service._update_settings(LLMSettings(system_instruction="Ask for a code."))
    assert service._backend_llm.has_function("lookup")
    assert service._backend_llm._function_is_node_transition("lookup")
    assert "Ask for a code." in service._backend_llm._settings.system_instruction
    assert service._backend_context.tools.standard_tools[0].name == "lookup"
    service.unregister_function("lookup")
    service._context.set_tools(ToolsSchema(standard_tools=[]))
    await service._update_settings(LLMSettings(system_instruction="Say goodbye."))
    assert not service._backend_llm.has_function("lookup")
    assert not is_given(service._backend_context.tools)
    assert "Say goodbye." in service._backend_llm._settings.system_instruction
    service._transport.connect.assert_not_awaited()
    await service._disconnect()


@pytest.mark.asyncio
async def test_greeting_once_uses_subscription_instruction_and_media_only_webrtc():
    service = make_service(ready=False)
    service._context = LLMContext()
    await service.process_frame(
        TTSSpeakFrame("Hello test caller"), FrameDirection.DOWNSTREAM
    )
    await service.process_frame(
        TTSSpeakFrame("Second greeting"), FrameDirection.DOWNSTREAM
    )
    connection = service._transport.connect.await_args.kwargs
    assert connection["access_token"] == "synthetic-oauth"
    assert connection["session"]["delegation"] == {"type": "client"}
    assert "synthetic-backend-key" not in str(connection)
    sent = [call.args[0] for call in service._transport.send_event.await_args_list]
    assert len(sent) == 1
    assert sent[0]["type"] == "session.instructions.append"
    assert "Hello test caller" in sent[0]["content"]
    service.push_frame.reset_mock()
    await service._on_subscription_event(
        {"type": "session.output_audio.delta", "delta": "AAAA"}
    )
    assert not service.push_frame.called
    await service._on_subscription_audio(bytes(960), 24000, 1)
    assert isinstance(service.push_frame.await_args.args[0], SpeechOutputAudioRawFrame)
    await service._disconnect()
    service._auth_service.acquire_session.return_value.release.assert_awaited_once()


@pytest.mark.asyncio
async def test_mute_preserves_input_clock_and_transport_resampler_guard():
    service = make_service()
    pcm = b"\x01\x02" * 160
    frame = InputAudioRawFrame(audio=pcm, sample_rate=16000, num_channels=1)
    await service.process_frame(UserMuteStartedFrame(), FrameDirection.DOWNSTREAM)
    assert service._transport.muted
    await service._send_user_audio(frame)
    assert service._transport.send_audio.await_args.args == (bytes(len(pcm)), 16000, 1)
    assert frame.audio == pcm
    await service.process_frame(UserMuteStoppedFrame(), FrameDirection.DOWNSTREAM)
    await service._send_user_audio(frame)
    assert service._transport.send_audio.await_args.args[0] == pcm
    await service._disconnect()


@pytest.mark.asyncio
async def test_delegation_duplicate_and_final_update_are_delivered_once():
    service = make_service()
    service._setup = SimpleNamespace(
        pipeline_worker=SimpleNamespace(),
        observer=None,
        enable_metrics=False,
        enable_usage_metrics=False,
    )

    async def answer(*args, on_update, **kwargs):
        await on_update(
            BackendOutput("private thought", is_thought=True, prefers_spoken=False)
        )
        await on_update(BackendOutput("Found the test entry.", is_final=True))
        return "Found the test entry."

    with patch(
        "api.services.pipecat.realtime.openai_live_subscription._delegate_to_backend",
        side_effect=answer,
    ) as delegate:
        evt = {"type": "delegation.created", "item": {"id": "d_1", "target": "client"}}
        await service._on_subscription_event(evt)
        await service._on_subscription_event(evt)
        await asyncio.gather(*list(service._delegation_tasks.values()))
        await service._on_subscription_event(evt)
        assert delegate.await_count == 1
    sent = [c.args[0] for c in service._transport.send_event.await_args_list]
    assert len(sent) == 2
    assert sent[0]["channel"] == "commentary"
    assert sent[1]["channel"] == "speakable"
    assert sent[1]["delegation_item_id"] == "d_1"
    await service._disconnect()


@pytest.mark.asyncio
async def test_correction_and_node_change_retire_delayed_output_and_tool_dispatch():
    service = make_service()
    service._setup = SimpleNamespace(
        pipeline_worker=SimpleNamespace(),
        observer=None,
        enable_metrics=False,
        enable_usage_metrics=False,
    )
    running, release = asyncio.Event(), asyncio.Event()

    async def answer(*args, on_update, **kwargs):
        running.set()
        await release.wait()
        await on_update(BackendOutput("obsolete answer", is_final=True))
        return "obsolete answer"

    with patch(
        "api.services.pipecat.realtime.openai_live_subscription._delegate_to_backend",
        side_effect=answer,
    ):
        delegation = asyncio.create_task(
            service._run_subscription_delegation(request(service))
        )
        await asyncio.wait_for(running.wait(), timeout=2)
        await service._accept_transcript("user", "Actually use the corrected entry.")
        release.set()
        await delegation
    assert not service._transport.send_event.called
    service._context = LLMContext(
        tools=ToolsSchema(standard_tools=[tool("transition")])
    )
    service._active_delegation = request(service)
    service.register_function(
        "transition", AsyncMock(), is_node_transition=True, cancellable_by_llm=False
    )
    await service._update_settings(LLMSettings(system_instruction="A different node."))
    call = FunctionCallFromLLM(
        function_name="transition",
        tool_call_id="stale_call",
        arguments={},
        context=service._backend_context,
    )
    service._backend_llm.broadcast_frame = AsyncMock()
    with patch.object(LLMService, "run_function_calls", new_callable=AsyncMock) as run:
        await service._backend_llm.run_function_calls([call])
        run.assert_not_awaited()
    assert service._backend_llm.broadcast_frame.called
    await service._disconnect()


@pytest.mark.asyncio
async def test_transition_waits_for_bot_audio_and_rechecks_current_request():
    service = make_service()
    service._active_delegation = request(service)
    service.register_function(
        "transition", AsyncMock(), is_node_transition=True, cancellable_by_llm=False
    )
    call = FunctionCallFromLLM(
        function_name="transition",
        tool_call_id="c1",
        arguments={},
        context=LLMContext(),
    )
    await service.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    with patch.object(LLMService, "run_function_calls", new_callable=AsyncMock) as run:
        pending = asyncio.create_task(service._backend_llm.run_function_calls([call]))
        await asyncio.sleep(0)
        run.assert_not_awaited()
        await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        await pending
        run.assert_awaited_once_with([call])
    await service._disconnect()


@pytest.mark.asyncio
async def test_transcript_finality_does_not_duplicate_partial_text():
    service = make_service()
    service.broadcast_frame = AsyncMock()
    await service._on_subscription_event(
        {
            "type": "session.input_transcript.added",
            "item": {"id": "t1", "text": "Test entry"},
        }
    )
    assert service._user_turn.open
    await service._on_subscription_event(
        {
            "type": "turn.done",
            "turn": {"id": "t1", "role": "user", "transcript": "Test entry please"},
        }
    )
    assert not service._user_turn.open
    assert service._take_transcript() == [
        {"role": "user", "content": "Test entry please"}
    ]
    await service._disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [TimeoutError("synthetic-secret"), RuntimeError("synthetic-secret")]
)
async def test_backend_failure_is_redacted_terminal_and_releases_lease(failure):
    service = make_service()
    service.FAILURE_SPEECH_START_TIMEOUT = 0.01
    service._setup = SimpleNamespace(
        pipeline_worker=SimpleNamespace(),
        observer=None,
        enable_metrics=False,
        enable_usage_metrics=False,
    )
    service._lease = service._auth_service.acquire_session.return_value
    with patch(
        "api.services.pipecat.realtime.openai_live_subscription._delegate_to_backend",
        side_effect=failure,
    ):
        await service._run_subscription_delegation(request(service))
    assert service._terminal
    assert "cannot confirm" in str(service._transport.send_event.await_args_list)
    assert "synthetic-secret" not in str(service.push_error.await_args_list)
    service._auth_service.acquire_session.return_value.release.assert_awaited_once()
    service._auth_service.aclose.assert_awaited_once()
    assert not service._delegation_tasks


@pytest.mark.asyncio
async def test_real_worker_job_calls_tool_changes_node_and_records_once():
    service = make_service(ready=False, mocked_frames=False)
    service._backend_llm._connect = AsyncMock()
    context = LLMContext(tools=ToolsSchema(standard_tools=[tool("lookup")]))
    service._context = context
    calls, callbacks = [], []

    async def handler(params):
        calls.append(params.tool_call_id)
        context.set_tools(ToolsSchema(standard_tools=[]))
        await service._update_settings(
            LLMSettings(system_instruction="Tell the test result.")
        )

        async def updated():
            callbacks.append("updated")

        await params.result_callback(
            {"entry": "test-found"},
            properties=FunctionCallResultProperties(on_context_updated=updated),
        )

    service.register_function(
        "lookup", handler, is_node_transition=True, cancellable_by_llm=False
    )
    inferred = 0

    async def infer(backend_context):
        nonlocal inferred
        inferred += 1
        if inferred == 1:
            await service._backend_llm.run_function_calls(
                [
                    FunctionCallFromLLM(
                        function_name="lookup",
                        tool_call_id="lookup_1",
                        arguments={},
                        context=backend_context,
                    )
                ]
            )
        else:
            await service._backend_llm.push_frame(LLMTextFrame("Found test-found."))

    service._backend_llm._process_context = infer
    aggregators = LLMContextAggregatorPair(context)
    pipeline = Pipeline([aggregators.user(), service, aggregators.assistant()])
    worker = PipelineWorker(pipeline, params=PipelineParams(), enable_rtvi=False)
    run = asyncio.create_task(run_pipeline_worker(worker))
    try:
        await wait_for_pipeline_worker_started(worker, timeout=3, run_task=run)
        # Opening context submits a real ClientDelegation worker job.
        await service._handle_context(context)
        await asyncio.wait_for(
            asyncio.gather(*list(service._delegation_tasks.values())), timeout=5
        )
        assert calls == ["lookup_1"]
        assert callbacks == ["updated"]
        assert inferred == 2
        assert not is_given(service._backend_context.tools)
        assert any(
            m.get("role") == "tool" and "test-found" in m.get("content", "")
            for m in context.messages
        )
        wire = [c.args[0] for c in service._transport.send_event.await_args_list]
        assert sum("Found test-found." in str(e) for e in wire) == 1
    finally:
        await worker.queue_frame(EndFrame())
        await asyncio.wait_for(run, timeout=5)
    assert service._lease is None
    assert not service._delegation_tasks


@pytest.mark.asyncio
async def test_delta_then_final_turn_does_not_retire_valid_delegation():
    service = make_service()
    service.broadcast_frame = AsyncMock()
    await service._on_subscription_event(
        {
            "type": "session.input_transcript.delta",
            "item_id": "partial_1",
            "delta": "Lookup the test entry.",
        }
    )
    active = request(service)
    revision = service._input_revision
    await service._on_subscription_event(
        {
            "type": "turn.done",
            "turn": {
                "id": "final_1",
                "role": "user",
                "transcript": "Lookup the test entry.",
            },
        }
    )
    assert service._input_revision == revision
    assert service._is_current(active)
    assert service._take_transcript() == [
        {"role": "user", "content": "Lookup the test entry."}
    ]
    await service._disconnect()


@pytest.mark.asyncio
async def test_cleanup_still_closes_auth_and_reports_terminal_error_when_redis_release_fails():
    service = make_service()
    lease = service._auth_service.acquire_session.return_value
    service._lease = lease
    lease.release.side_effect = RuntimeError("do not expose synthetic secret")
    await service._fail("Subscription connection unavailable.")
    service._transport.close.assert_awaited_once()
    service._auth_service.aclose.assert_awaited_once()
    service.push_error.assert_awaited_once_with(
        error_msg="Subscription connection unavailable.",
        force_treat_as_permanent=True,
    )
    assert service._lease is None


@pytest.mark.asyncio
async def test_real_pipecat_engine_transition_preserves_delegation_and_worker_cleanup(
    three_node_workflow_no_variable_extraction,
):
    from api.services.workflow.pipecat_engine import PipecatEngine

    service = make_service(ready=False, mocked_frames=False)
    service._backend_llm._connect = AsyncMock()
    context = LLMContext()
    service._context = context
    engine = PipecatEngine(
        llm=service,
        context=context,
        workflow=three_node_workflow_no_variable_extraction,
        call_context_vars={},
        is_realtime=True,
    )
    await engine.set_node("start")
    first_tool = context.tools.standard_tools[0].name
    calls = []

    async def infer(backend_context):
        if not calls:
            calls.append(first_tool)
            await service._backend_llm.run_function_calls(
                [
                    FunctionCallFromLLM(
                        function_name=first_tool,
                        tool_call_id="workflow_transition_1",
                        arguments={},
                        context=backend_context,
                    )
                ]
            )
        else:
            await service._backend_llm.push_frame(
                LLMTextFrame("The test workflow moved to collect information.")
            )

    service._backend_llm._process_context = infer
    aggregators = LLMContextAggregatorPair(context)
    worker = PipelineWorker(
        Pipeline([aggregators.user(), service, aggregators.assistant()]),
        params=PipelineParams(),
        enable_rtvi=False,
    )
    engine.set_task(worker)
    run = asyncio.create_task(run_pipeline_worker(worker))
    try:
        await wait_for_pipeline_worker_started(worker, timeout=3, run_task=run)
        await service._handle_context(context)
        await asyncio.wait_for(
            asyncio.gather(*list(service._delegation_tasks.values())), timeout=5
        )
        assert engine._current_node.id == "agent"
        assert calls == [first_tool]
        assert first_tool not in {
            t.name for t in service._backend_context.tools.standard_tools
        }
        assert (
            "Agent Node System Prompt"
            in service._backend_llm._settings.system_instruction
        )
        assert any(
            m.get("role") == "tool"
            and m.get("tool_call_id") == "workflow_transition_1"
            and "done" in m.get("content", "")
            for m in context.messages
        )
        wire = [c.args[0] for c in service._transport.send_event.await_args_list]
        assert sum("test workflow moved" in str(e) for e in wire) == 1
        assert wire[-1]["type"] == "session.commentary.append"
        assert wire[-1]["delegation_id"] is None
        assert not service._terminal
    finally:
        await worker.queue_frame(EndFrame())
        await asyncio.wait_for(run, timeout=5)
    assert service._backend_worker.has_finished()
    assert service._lease is None
    assert not service._delegation_tasks


@pytest.mark.asyncio
async def test_disconnect_survives_caller_cancel_and_concurrent_cleanup_once():
    service = make_service()
    service._lease = service._auth_service.acquire_session.return_value
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_close():
        started.set()
        await release.wait()

    service._transport.close.side_effect = slow_close
    first = asyncio.create_task(service._disconnect())
    await asyncio.wait_for(started.wait(), timeout=2)
    second = asyncio.create_task(service._disconnect())
    first.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    await service._disconnect()
    service._transport.close.assert_awaited_once()
    service._auth_service.acquire_session.return_value.release.assert_awaited_once()
    service._auth_service.aclose.assert_awaited_once()
    assert service._lease is None
    assert not service._delegation_tasks


@pytest.mark.asyncio
async def test_subscription_usage_totals_emitted_once_and_never_as_api_live_seconds():
    service = make_service(ready=False)
    service._context = LLMContext()
    await service.process_frame(
        TTSSpeakFrame("Test greeting"), FrameDirection.DOWNSTREAM
    )
    await service._send_user_audio(InputAudioRawFrame(bytes(3200), 16000, 1))
    await service._on_subscription_audio(bytes(9600), 24000, 1)
    await service._disconnect()
    await service._disconnect()
    metrics = [
        c.args[0]
        for c in service.push_frame.await_args_list
        if isinstance(c.args[0], MetricsFrame)
    ]
    assert len(metrics) == 1
    usage = metrics[0].data[0]
    assert usage.model == "gpt-live-1-codex"
    assert usage.input_audio_seconds == pytest.approx(0.1)
    assert usage.output_audio_seconds == pytest.approx(0.2)
    assert usage.session_seconds >= 0
    assert usage.session_state == "ended"
    assert not hasattr(usage, "seconds")


@pytest.mark.asyncio
async def test_stale_queued_delegation_preserves_undelivered_transcript():
    service = make_service()
    service.broadcast_frame = AsyncMock()
    service._setup = SimpleNamespace(
        pipeline_worker=SimpleNamespace(),
        observer=None,
        enable_metrics=False,
        enable_usage_metrics=False,
    )
    running, release = asyncio.Event(), asyncio.Event()
    requests = []

    async def answer(*args, request, on_update, **kwargs):
        requests.append(request)
        if len(requests) == 1:
            running.set()
            await release.wait()
        await on_update(BackendOutput("Test result", is_final=True))
        return "Test result"

    def event(id):
        return {"type": "delegation.created", "item": {"id": id, "target": "client"}}

    with patch(
        "api.services.pipecat.realtime.openai_live_subscription._delegate_to_backend",
        side_effect=answer,
    ):
        await service._accept_transcript("user", "Find opening times.")
        await service._on_subscription_event(event("a"))
        await asyncio.wait_for(running.wait(), timeout=2)
        await service._accept_transcript("user", "Reserve Tuesday")
        await service._on_subscription_event(event("b"))
        await service._accept_transcript("user", " at 3pm.")
        await service._on_subscription_event(event("c"))
        release.set()
        await asyncio.wait_for(
            asyncio.gather(*list(service._delegation_tasks.values())), timeout=2
        )
    assert len(requests) == 2
    assert "Reserve Tuesday at 3pm." in requests[-1]
    wire = [c.args[0] for c in service._transport.send_event.await_args_list]
    assert len(wire) == 1
    assert wire[0]["delegation_item_id"] == "c"
    await service._disconnect()


@pytest.mark.asyncio
async def test_real_permanent_error_is_terminal_in_dograh_funnel():
    from pipecat.frames.frames import ErrorFrame
    from pipecat.processors.frame_processor import FrameProcessor

    from api.services.pipecat.termination_funnel_processor import is_terminal_error

    service = make_service()
    service.push_error = FrameProcessor.push_error.__get__(service)
    await service._fail("Subscription voice unavailable.")
    errors = [
        c.args[0]
        for c in service.push_frame.await_args_list
        if isinstance(c.args[0], ErrorFrame)
    ]
    assert len(errors) == 1
    assert errors[0].exception is None
    assert is_terminal_error(errors[0])
    assert not service.is_usable


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_event",
    [
        {"type": "error", "error": {"code": "rate_limited"}},
        {"type": "session.closed"},
    ],
)
async def test_actual_transport_callback_terminates_wrapper_without_cleanup_cycle(
    terminal_event,
):
    import aiortc
    import av

    from api.services.pipecat.realtime.openai_live_subscription_transport import (
        OpenAILiveSubscriptionTransport,
    )
    from api.tests.test_openai_subscription_transport import (
        FakeAiohttp,
        FakeHTTP,
        FakePeer,
    )

    service = make_service(ready=False)
    peer, http, aio = FakePeer(), FakeHTTP(), FakeAiohttp()
    service._transport = OpenAILiveSubscriptionTransport(
        on_event=service._on_subscription_event,
        on_audio=service._on_subscription_audio,
        http_client=http,
        aiohttp_module=aio,
        av_module=av,
        rtc_module=SimpleNamespace(
            AudioStreamTrack=aiortc.AudioStreamTrack,
            RTCSessionDescription=aiortc.RTCSessionDescription,
            RTCPeerConnection=lambda: peer,
            mediastreams=aiortc.mediastreams,
        ),
    )
    service._context = LLMContext()
    await service.process_frame(
        TTSSpeakFrame("Test greeting"), FrameDirection.DOWNSTREAM
    )
    await aio.socket.emit(terminal_event)

    async def completed():
        while service._terminal_event_task is None:
            await asyncio.sleep(0)
        await service._terminal_event_task

    await asyncio.wait_for(completed(), timeout=2)
    assert service._terminal
    assert peer.closed and aio.closed and aio.socket.closed
    assert not service._transport._tasks
    service._auth_service.acquire_session.return_value.release.assert_awaited_once()
    service._auth_service.aclose.assert_awaited_once()
    service.push_error.assert_awaited_once()
    await service._disconnect()


@pytest.mark.asyncio
async def test_backend_failure_keeps_voice_open_until_observed_new_playback_stops():
    service = make_service()
    service._setup = SimpleNamespace(
        pipeline_worker=SimpleNamespace(),
        observer=None,
        enable_metrics=False,
        enable_usage_metrics=False,
    )
    sent = asyncio.Event()

    async def send(_event):
        sent.set()

    service._transport.send_event.side_effect = send
    with patch(
        "api.services.pipecat.realtime.openai_live_subscription._delegate_to_backend",
        side_effect=RuntimeError("synthetic failure"),
    ):
        failed = asyncio.create_task(
            service._run_subscription_delegation(request(service))
        )
        await asyncio.wait_for(sent.wait(), timeout=2)
        # A previous utterance ending is not proof the requested failure played.
        await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        await asyncio.sleep(0)
        assert not failed.done()
        service._transport.close.assert_not_awaited()
        await service.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        await service._on_subscription_audio(bytes(960), 24000, 1)
        await asyncio.sleep(0)
        service._transport.close.assert_not_awaited()
        assert not service._terminal
        await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        await asyncio.wait_for(failed, timeout=2)
    service._transport.close.assert_awaited_once()
    service.push_error.assert_awaited_once()
    assert service._terminal


@pytest.mark.asyncio
@pytest.mark.parametrize("observe_start", [False, True])
async def test_backend_failure_start_and_stop_timeouts_both_terminate(observe_start):
    service = make_service()
    service.FAILURE_SPEECH_START_TIMEOUT = 0.01
    service.FAILURE_SPEECH_PLAYBACK_TIMEOUT = 0.01
    sent = asyncio.Event()

    async def send(_event):
        sent.set()

    service._transport.send_event.side_effect = send
    failed = asyncio.create_task(
        service._finish_backend_failure(
            request(service), "A safe failure message", "Backend failed."
        )
    )
    await asyncio.wait_for(sent.wait(), timeout=2)
    if observe_start:
        await service.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    await asyncio.wait_for(failed, timeout=2)
    assert service._terminal
    service._transport.close.assert_awaited_once()
    service.push_error.assert_awaited_once()


@pytest.mark.asyncio
async def test_voice_failure_interrupts_backend_failure_playback_wait_immediately():
    service = make_service()
    sent = asyncio.Event()

    async def send(_event):
        sent.set()

    service._transport.send_event.side_effect = send
    failed = asyncio.create_task(
        service._finish_backend_failure(
            request(service), "A safe failure message", "Backend failed."
        )
    )
    service._delegation_tasks["test_failure"] = failed
    await asyncio.wait_for(sent.wait(), timeout=2)
    await service._on_subscription_event(
        {"type": "error", "error": {"code": "connection_lost"}}
    )
    await asyncio.wait_for(service._terminal_event_task, timeout=2)
    assert failed.cancelled()
    assert service._terminal
    service._transport.close.assert_awaited_once()
    service.push_error.assert_awaited_once()


@pytest.mark.asyncio
async def test_backend_failure_drains_active_playback_before_arming_announcement():
    service = make_service()
    service.FAILURE_SPEECH_START_TIMEOUT = 0.01
    sent = asyncio.Event()

    async def send(_event):
        sent.set()

    service._transport.send_event.side_effect = send
    await service.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    failed = asyncio.create_task(
        service._finish_backend_failure(
            request(service), "A safe failure message", "Backend failed."
        )
    )
    # Continuous existing speech may exceed the new-speech start deadline.
    await asyncio.sleep(0.03)
    service._transport.send_event.assert_not_awaited()
    service._transport.close.assert_not_awaited()
    assert not failed.done()
    await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    await asyncio.wait_for(sent.wait(), timeout=2)
    service._transport.close.assert_not_awaited()
    await service.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    await service._on_subscription_audio(bytes(960), 24000, 1)
    service._transport.close.assert_not_awaited()
    await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    await asyncio.wait_for(failed, timeout=2)
    service._transport.close.assert_awaited_once()
    service.push_error.assert_awaited_once()


@pytest.mark.asyncio
async def test_backend_failure_existing_playback_timeout_is_bounded():
    service = make_service()
    service.FAILURE_SPEECH_PLAYBACK_TIMEOUT = 0.01
    await service.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    await asyncio.wait_for(
        service._finish_backend_failure(
            request(service), "A safe failure message", "Backend failed."
        ),
        timeout=2,
    )
    service._transport.send_event.assert_not_awaited()
    service._transport.close.assert_awaited_once()
    service.push_error.assert_awaited_once()
    assert service._terminal
