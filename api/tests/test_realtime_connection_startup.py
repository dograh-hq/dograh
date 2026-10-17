"""Call playback starts while websocket providers connect, with owned teardown."""

import ast
import asyncio
import inspect
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import EndFrame, InputAudioRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import ProcessorUnusablePolicy, WorkerParams
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.openai.live.llm import OpenAILiveLLMService
from pipecat.services.openai.realtime.llm import OpenAIRealtimeLLMService
from pipecat.services.xai.realtime.llm import GrokRealtimeLLMService
from pipecat.tests.mock_transport import MockOutputTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.utils.asyncio.task_manager import TaskManager

from api.services.pipecat.call_opening import OpeningInputGate
from api.services.pipecat.pipeline_worker import DograhPipelineWorker
from api.services.pipecat.realtime.azure_realtime import DograhAzureRealtimeLLMService
from api.services.pipecat.realtime.connection import BackgroundConnectionMixin
from api.services.pipecat.realtime.grok_realtime import DograhGrokRealtimeLLMService
from api.services.pipecat.realtime.openai_live import DograhOpenAILiveLLMService
from api.services.pipecat.realtime.openai_realtime import DograhOpenAIRealtimeLLMService
from api.services.pipecat.recording_audio_cache import RecordingAudio
from api.services.workflow.pipecat_engine import PipecatEngine
from api.tests.test_call_start_sequence import Call


@pytest.mark.parametrize(
    "upstream", [OpenAIRealtimeLLMService, OpenAILiveLLMService, GrokRealtimeLLMService]
)
def test_upstream_setup_finishes_local_initialization_before_connect(upstream):
    # BackgroundConnectionMixin relies on _connect being setup's last step.
    # An upstream phase added after it must be reviewed for readiness coupling.
    setup = ast.parse(textwrap.dedent(inspect.getsource(upstream.setup))).body[0]
    assert ast.unparse(setup.body[-1]) == "await self._connect()"
    assert ast.unparse(setup.body[1]) == "await super().setup(setup)"


class Socket:
    def __init__(self):
        self.send = AsyncMock()
        self.close = AsyncMock()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.Event().wait()


@pytest.fixture(params=["openai", "azure", "grok", "live"])
def connecting(request, monkeypatch):
    provider = request.param
    cls, module, kwargs = {
        "openai": (DograhOpenAIRealtimeLLMService, "openai.realtime", {}),
        "azure": (
            DograhAzureRealtimeLLMService,
            "azure.realtime",
            {"base_url": "wss://example.test/openai/v1/realtime"},
        ),
        "grok": (DograhGrokRealtimeLLMService, "xai.realtime", {}),
        "live": (
            DograhOpenAILiveLLMService,
            "openai.live",
            {"backend_model": "gpt-5.4-mini"},
        ),
    }[provider]
    state = SimpleNamespace(
        service=cls(api_key="test", **kwargs),
        entered=asyncio.Event(),
        release=asyncio.Event(),
        cancelled=asyncio.Event(),
        socket=Socket(),
    )

    async def connect(**kwargs):
        state.entered.set()
        try:
            await state.release.wait()
        except asyncio.CancelledError:
            state.cancelled.set()
            raise
        return state.socket

    state.connect = AsyncMock(side_effect=connect)
    monkeypatch.setattr(
        f"pipecat.services.{module}.llm.websocket_connect", state.connect
    )
    return state


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_recorded_opening_reaches_output_while_connection_is_blocked(
    connecting, simple_workflow, monkeypatch
):
    state = connecting
    call = Call(monkeypatch, fetch_task=None, is_child=False)
    engine = call.engine
    engine._is_realtime = True
    engine.active_agent.llm = state.service
    engine.active_agent.workflow = simple_workflow
    node = simple_workflow.nodes[simple_workflow.start_node_id]
    node.delayed_start = False
    node.greeting_type = "audio"
    node.greeting_recording_id = "42"
    engine.set_context(LLMContext())
    engine._audio_config = SimpleNamespace(
        pipeline_sample_rate=16000, transport_out_sample_rate=16000
    )
    engine.queue_node_opening = PipecatEngine.queue_node_opening.__get__(engine)
    fetch = AsyncMock(return_value=RecordingAudio(b"\x00\x10" * 1600, "Welcome."))
    engine.set_fetch_recording_audio(fetch)
    retained = engine.opening.input_gate = OpeningInputGate()
    output = MockOutputTransport(
        params=TransportParams(
            audio_out_enabled=True,
            audio_out_sample_rate=16000,
            audio_out_end_silence_secs=0,
        )
    )
    worker = DograhPipelineWorker(
        Pipeline([retained, state.service, output]), enable_rtvi=False
    )
    engine.call_worker = worker
    engine.set_transport_output(output)
    manager = TaskManager()
    started = asyncio.Event()

    @worker.event_handler("on_pipeline_started")
    async def on_started(*args):
        # Exercise the actual opening callback from register_event_handlers.
        await call.task.handlers["on_pipeline_started"](*args)
        started.set()

    await call.transport.handlers["on_client_connected"](call.transport, None)
    running = asyncio.create_task(worker.run(WorkerParams(task_manager=manager)))
    try:
        await asyncio.wait_for(state.entered.wait(), 2)
        await asyncio.wait_for(started.wait(), 2)
        await worker.queue_frame(InputAudioRawFrame(bytes(640), 16000, 1))
        await eventually(lambda: output._frames_written > 0)
        fetch.assert_awaited_once_with(recording_pk=42)
        assert not state.release.is_set()
        assert state.service._websocket is None
        assert retained._holding
        assert not state.service.opening_input_ready
        state.socket.send.assert_not_awaited()

        # The same pending connection completes, without opening another socket.
        state.release.set()
        await eventually(lambda: state.service._websocket is state.socket)
        state.connect.assert_awaited_once()
    finally:
        await engine.opening.close()
        engine.speech_playback.cancel_all()
        await engine.cancel_initial_agent_startup()
        await worker.cancel()
        await asyncio.wait_for(running, 3)
    state.socket.close.assert_awaited_once()
    assert state.service._startup_connection_task is None
    assert not manager.current_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_path", ["cancel", "end", "external"])
async def test_shutdown_cancels_pending_connection_without_late_socket(
    connecting, exit_path
):
    state = connecting
    worker = DograhPipelineWorker(Pipeline([state.service]), enable_rtvi=False)
    manager = TaskManager()
    started = asyncio.Event()

    @worker.event_handler("on_pipeline_started")
    async def on_started(*_):
        started.set()

    running = asyncio.create_task(worker.run(WorkerParams(task_manager=manager)))
    try:
        await asyncio.wait_for(state.entered.wait(), 2)
        await asyncio.wait_for(started.wait(), 2)
        if exit_path == "external":
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(running, 2)
        else:
            if exit_path == "end":
                await worker.queue_frame(EndFrame())
            else:
                await worker.cancel()
            await asyncio.wait_for(running, 2)
        assert state.cancelled.is_set()
        assert state.service._startup_connection_task is None
        state.release.set()
        await asyncio.sleep(0)
        assert state.service._websocket is None
        assert not manager.current_tasks()
    finally:
        if not running.done():
            await worker.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_reconnect_outside_setup_still_awaits_connection(connecting):
    state = connecting
    worker = DograhPipelineWorker(Pipeline([state.service]), enable_rtvi=False)
    manager = TaskManager()
    started = asyncio.Event()

    @worker.event_handler("on_pipeline_started")
    async def on_started(*_):
        started.set()

    running = asyncio.create_task(worker.run(WorkerParams(task_manager=manager)))
    reconnect = None
    try:
        await asyncio.wait_for(state.entered.wait(), 2)
        await asyncio.wait_for(started.wait(), 2)
        await state.service._disconnect()
        reconnect = asyncio.create_task(state.service._connect())
        await eventually(lambda: state.connect.await_count == 2)
        assert not reconnect.done()
        state.release.set()
        await asyncio.wait_for(reconnect, 2)
        assert state.service._websocket is state.socket
    finally:
        if reconnect is not None:
            reconnect.cancel()
            await asyncio.gather(reconnect, return_exceptions=True)
        await worker.cancel()
        await asyncio.wait_for(running, 2)
    assert not manager.current_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize("connected", [False, True])
async def test_failed_pipeline_setup_cleans_pending_or_connected_provider(
    connecting, connected
):
    state = connecting

    class FailingSetup(DograhPipelineWorker):
        async def _setup(self, params):
            await super()._setup(params)
            await asyncio.wait_for(state.entered.wait(), 2)
            if connected:
                state.release.set()
                await eventually(lambda: state.service._websocket is state.socket)
            raise RuntimeError("another processor failed setup")

    worker = FailingSetup(Pipeline([state.service]), enable_rtvi=False)
    manager = TaskManager()
    with pytest.raises(RuntimeError, match="another processor failed setup"):
        await asyncio.wait_for(worker.run(WorkerParams(task_manager=manager)), 2)
    assert state.service._startup_connection_task is None
    assert state.service._websocket is None
    assert not manager.current_tasks()
    if connected:
        state.socket.close.assert_awaited_once()
    else:
        assert state.cancelled.is_set()


@pytest.mark.asyncio
async def test_unexpected_connection_exception_cancels_worker(connecting, monkeypatch):
    service = connecting.service
    mro = type(service).__mro__
    upstream = next(
        cls
        for cls in mro[mro.index(BackgroundConnectionMixin) + 1 :]
        if "_connect" in vars(cls)
    )
    failure = RuntimeError("unexpected provider connection failure")

    async def fail(_service):
        raise failure

    monkeypatch.setattr(upstream, "_connect", fail)
    report = AsyncMock(wraps=service.push_error)
    monkeypatch.setattr(service, "push_error", report)
    worker = DograhPipelineWorker(
        Pipeline([service]),
        enable_rtvi=False,
        processor_unusable_policy=ProcessorUnusablePolicy.CANCEL,
    )
    manager = TaskManager()
    await asyncio.wait_for(worker.run(WorkerParams(task_manager=manager)), 2)
    report.assert_awaited_once_with(
        error_msg=f"Realtime startup connection failed: {failure}",
        exception=failure,
        force_treat_as_permanent=True,
    )
    assert worker.has_finished()
    assert service._startup_connection_task is None
    assert not manager.current_tasks()
