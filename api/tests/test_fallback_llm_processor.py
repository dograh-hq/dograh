"""Exercise Dograh's race through unchanged Gemini services and frame queues."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from google.genai import Client
from google.genai.types import (
    Candidate,
    Content,
    FunctionCall,
    GenerateContentResponse,
    GenerateContentResponseUsageMetadata,
    HttpOptions,
    Part,
)
from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import (
    ErrorFrame,
    FunctionCallsFromLLMInfoFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
    MetricsFrame,
    StartFrame,
)
from pipecat.metrics.metrics import LLMUsageMetricsData, TTFBMetricsData
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import (
    FrameDirection,
    FrameProcessor,
    FrameProcessorSetup,
)
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.settings import LLMSettings
from pipecat.utils.asyncio.task_manager import TaskManager

from api.services.pipecat.fallback_llm import FallbackLLMProcessor

PRIMARY = "gemini-3.5-flash"
BACKUP = "gemini-3.1-flash-lite"
DOWN = FrameDirection.DOWNSTREAM


def chunk(text="answer", *, parts=None, tokens=None):
    return GenerateContentResponse(
        candidates=[
            Candidate(content=Content(role="model", parts=parts or [Part(text=text)]))
        ],
        usage_metadata=GenerateContentResponseUsageMetadata(
            candidates_token_count=tokens
        )
        if tokens is not None
        else None,
    )


class Stream:
    def __init__(self, *steps):
        self.steps = steps
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    async def __call__(self):
        self.started.set()
        try:
            for step in self.steps:
                if isinstance(step, asyncio.Event):
                    await step.wait()
                elif isinstance(step, Exception):
                    raise step
                else:
                    yield step
        finally:
            self.closed.set()


class Capture(FrameProcessor):
    def __init__(self):
        super().__init__(enable_direct_mode=True)
        self.frames = []
        self.started = asyncio.Event()

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        self.frames.append(frame)
        if isinstance(frame, StartFrame):
            self.started.set()
        await self.push_frame(frame, direction)


@pytest_asyncio.fixture
async def harness():
    services = [
        GoogleLLMService(
            api_key="test-key",
            settings=GoogleLLMService.Settings(model=model),
            enable_direct_mode=True,
            stream_idle_timeout_secs=0.3,
        )
        for model in (PRIMARY, BACKUP)
    ]
    llm = FallbackLLMProcessor(
        *services, fallback_after_secs=0.02, first_output_timeout_secs=0.3
    )
    upstream, downstream = Capture(), Capture()
    pipeline = Pipeline([upstream, llm, downstream])
    manager = TaskManager()
    await pipeline.setup(
        FrameProcessorSetup(
            clock=SystemClock(),
            task_manager=manager,
            pipeline_worker=SimpleNamespace(app_resources={}, worker_runner=None),
            enable_metrics=True,
            enable_usage_metrics=True,
        )
    )
    await pipeline.queue_frame(StartFrame())
    await asyncio.wait_for(downstream.started.wait(), 1)
    yield SimpleNamespace(
        llm=llm,
        pipeline=pipeline,
        upstream=upstream,
        downstream=downstream,
        manager=manager,
    )
    await pipeline.cleanup()
    assert not manager.current_tasks()


def install_streams(harness, monkeypatch, primary, backup):
    for service, stream in zip(
        (harness.llm.primary, harness.llm.fallback), (primary, backup)
    ):
        monkeypatch.setattr(
            service, "_stream_content", AsyncMock(side_effect=lambda _, s=stream: s())
        )


async def generate(harness, *, context=None, speculation=False):
    done = asyncio.Event()

    async def completed(*_):
        done.set()

    await harness.llm.queue_frame(
        LLMContextFrame(context or LLMContext(), speculation=speculation),
        callback=completed,
    )
    await asyncio.wait_for(done.wait(), 1)
    return [
        frame.text
        for frame in harness.downstream.frames
        if isinstance(frame, LLMTextFrame)
    ]


@pytest.mark.asyncio
async def test_fast_primary_never_starts_backup(harness, monkeypatch):
    primary, backup = Stream(chunk("primary")), Stream(chunk("backup"))
    install_streams(harness, monkeypatch, primary, backup)
    assert await generate(harness) == ["primary"]
    assert not backup.started.is_set()
    assert primary.closed.is_set()
    assert harness.llm.fallback_metrics == {"started": 0, "won": 0}


@pytest.mark.asyncio
async def test_backup_wins_and_next_generation_restarts_primary(harness, monkeypatch):
    primary, backup = Stream(asyncio.Event()), Stream(chunk("backup"))
    install_streams(harness, monkeypatch, primary, backup)
    assert await generate(harness) == ["backup"]
    assert primary.closed.is_set() and backup.closed.is_set()
    assert harness.llm.fallback_metrics == {"started": 1, "won": 1}
    primary.steps = (chunk("primary"),)
    assert await generate(harness) == ["backup", "primary"]
    frames = harness.downstream.frames
    assert sum(isinstance(f, LLMFullResponseStartFrame) for f in frames) == 2
    assert sum(isinstance(f, LLMFullResponseEndFrame) for f in frames) == 2


@pytest.mark.asyncio
async def test_primary_wins_after_backup_starts(harness, monkeypatch):
    release = asyncio.Event()
    primary, backup = Stream(release, chunk("primary")), Stream(asyncio.Event())
    install_streams(harness, monkeypatch, primary, backup)
    task = asyncio.create_task(generate(harness))
    await asyncio.wait_for(backup.started.wait(), 1)
    release.set()
    assert await task == ["primary"]
    assert backup.closed.is_set()
    assert harness.llm.fallback_metrics == {"started": 1, "won": 0}


@pytest.mark.asyncio
async def test_error_starts_backup_immediately(harness, monkeypatch):
    harness.llm._fallback_after_secs = 10
    install_streams(
        harness,
        monkeypatch,
        Stream(ConnectionError("primary")),
        Stream(chunk("backup")),
    )
    assert await generate(harness) == ["backup"]
    assert not any(isinstance(f, ErrorFrame) for f in harness.upstream.frames)


@pytest.mark.asyncio
async def test_backup_error_does_not_abandon_primary(harness, monkeypatch):
    release = asyncio.Event()
    primary, backup = (
        Stream(release, chunk("primary")),
        Stream(ConnectionError("backup")),
    )
    install_streams(harness, monkeypatch, primary, backup)
    task = asyncio.create_task(generate(harness))
    await asyncio.wait_for(backup.closed.wait(), 1)
    release.set()
    assert await task == ["primary"]
    assert not any(isinstance(f, ErrorFrame) for f in harness.upstream.frames)


@pytest.mark.asyncio
async def test_both_fail_emit_one_error_and_one_response(harness, monkeypatch):
    install_streams(
        harness,
        monkeypatch,
        Stream(TimeoutError("primary")),
        Stream(ConnectionError("backup")),
    )
    assert await generate(harness) == []
    assert sum(isinstance(f, ErrorFrame) for f in harness.upstream.frames) == 1
    assert (
        sum(isinstance(f, LLMFullResponseStartFrame) for f in harness.downstream.frames)
        == 1
    )
    assert (
        sum(isinstance(f, LLMFullResponseEndFrame) for f in harness.downstream.frames)
        == 1
    )


@pytest.mark.asyncio
async def test_winner_error_never_switches_after_text(harness, monkeypatch):
    backup = Stream(chunk("backup"))
    install_streams(
        harness,
        monkeypatch,
        Stream(chunk("partial"), ConnectionError("broken")),
        backup,
    )
    assert await generate(harness) == ["partial"]
    assert not backup.started.is_set()
    assert sum(isinstance(f, ErrorFrame) for f in harness.upstream.frames) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("start_backup", [False, True])
async def test_interruption_closes_requests_and_next_turn_works(
    harness, monkeypatch, start_backup
):
    primary, backup = Stream(asyncio.Event()), Stream(asyncio.Event())
    install_streams(harness, monkeypatch, primary, backup)
    await harness.llm.queue_frame(LLMContextFrame(LLMContext()))
    await asyncio.wait_for((backup if start_backup else primary).started.wait(), 1)
    done = asyncio.Event()

    async def interrupted(*_):
        done.set()

    await harness.llm.queue_frame(InterruptionFrame(), callback=interrupted)
    await asyncio.wait_for(done.wait(), 1)
    assert primary.closed.is_set()
    assert backup.closed.is_set() == start_backup
    assert not any(isinstance(f, ErrorFrame) for f in harness.upstream.frames)
    primary.steps = (chunk("next"),)
    assert await generate(harness) == ["next"]


@pytest.mark.asyncio
async def test_empty_stream_fails_over(harness, monkeypatch):
    install_streams(
        harness, monkeypatch, Stream(GenerateContentResponse()), Stream(chunk("backup"))
    )
    assert await generate(harness) == ["backup"]


@pytest.mark.asyncio
async def test_setup_deadline_covers_both_requests(harness, monkeypatch):
    harness.llm._first_output_timeout_secs = 0.04

    async def stuck(_):
        await asyncio.Event().wait()

    for service in (harness.llm.primary, harness.llm.fallback):
        monkeypatch.setattr(service, "_stream_content", stuck)
    assert await generate(harness) == []
    assert sum(isinstance(f, ErrorFrame) for f in harness.upstream.frames) == 1


@pytest.mark.asyncio
async def test_only_winner_tools_signatures_and_usage_escape(harness, monkeypatch):
    release = asyncio.Event()
    tool_parts = [
        Part(
            function_call=FunctionCall(name="book", args={}, id="booking"),
            thought_signature=b"signature",
        )
    ]
    primary = Stream(release, chunk(parts=tool_parts, tokens=10))
    backup = Stream(release, chunk(parts=tool_parts, tokens=20))
    install_streams(harness, monkeypatch, primary, backup)
    handler = AsyncMock()
    prepared = AsyncMock()
    harness.llm.register_function("book", handler)
    harness.llm.add_event_handler("on_function_calls_prepared", prepared)
    task = asyncio.create_task(generate(harness))
    await asyncio.wait_for(backup.started.wait(), 1)
    release.set()
    await task
    await asyncio.sleep(0)
    handler.assert_awaited_once()
    prepared.assert_awaited_once()
    for kind in (
        FunctionCallsFromLLMInfoFrame,
        LLMMessagesAppendFrame,
        LLMFullResponseStartFrame,
        LLMFullResponseEndFrame,
    ):
        assert sum(isinstance(f, kind) for f in harness.downstream.frames) == 1
    usage = [
        data
        for f in harness.downstream.frames
        if isinstance(f, MetricsFrame)
        for data in f.data
        if isinstance(data, LLMUsageMetricsData)
    ]
    assert len(usage) == 1
    assert (usage[0].model, usage[0].value.completion_tokens) in [
        (PRIMARY, 10),
        (BACKUP, 20),
    ]


@pytest.mark.asyncio
async def test_runtime_instructions_reach_both_and_inference_uses_primary(
    harness, monkeypatch
):
    await harness.llm._update_settings(
        LLMSettings(system_instruction="new instructions")
    )
    assert harness.llm.primary._settings.system_instruction == "new instructions"
    assert harness.llm.fallback._settings.system_instruction == "new instructions"
    assert harness.llm.fallback._settings.model == BACKUP
    infer = AsyncMock(return_value="one shot")
    monkeypatch.setattr(harness.llm.primary, "run_inference", infer)
    assert await harness.llm.run_inference(LLMContext()) == "one shot"


@pytest.mark.asyncio
async def test_backup_ttfb_includes_delay(harness, monkeypatch):
    install_streams(
        harness, monkeypatch, Stream(asyncio.Event()), Stream(chunk("backup"))
    )
    await generate(harness)
    timings = [
        data
        for f in harness.downstream.frames
        if isinstance(f, MetricsFrame)
        for data in f.data
        if isinstance(data, TTFBMetricsData)
    ]
    assert len(timings) == 1
    assert timings[0].value >= harness.llm._fallback_after_secs


@pytest.mark.asyncio
@pytest.mark.parametrize("simultaneous_output", [False, True])
async def test_real_sdk_closes_both_http_responses(harness, simultaneous_output):
    both_started = asyncio.Event()
    bodies = []

    class ResponseBody(httpx.AsyncByteStream):
        def __init__(self, primary):
            self.primary = primary
            self.closed = asyncio.Event()

        async def __aiter__(self):
            if self.primary:
                await (both_started if simultaneous_output else asyncio.Event()).wait()
            yield b'data: {"candidates":[{"content":{"role":"model","parts":[{"text":"answer"}]}}]}\n\n'
            # Keep a losing response open after its first chunk, too.
            if self.primary:
                await asyncio.Event().wait()

        async def aclose(self):
            self.closed.set()

    async def handle(request):
        body = ResponseBody(PRIMARY in request.url.path)
        bodies.append(body)
        if len(bodies) == 2:
            both_started.set()
        return httpx.Response(
            200, stream=body, headers={"Content-Type": "text/event-stream"}
        )

    clients = []
    try:
        for service in (harness.llm.primary, harness.llm.fallback):
            await service._close_client()
            client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
            clients.append(client)
            service._client = Client(
                api_key="test", http_options=HttpOptions(httpx_async_client=client)
            )
        assert await generate(
            harness, context=LLMContext([{"role": "user", "content": "Hi"}])
        ) == ["answer"]
        assert len(bodies) == 2
        # Check before closing the clients: client teardown would mask leaks.
        async with asyncio.timeout(0.5):
            for body in bodies:
                await body.closed.wait()
    finally:
        for client in clients:
            await client.aclose()


@pytest.mark.asyncio
async def test_slow_loser_cleanup_does_not_delay_output(harness, monkeypatch):
    cleanup_started, release_cleanup, output = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )

    async def primary():
        try:
            await asyncio.Event().wait()
            yield chunk("unused")
        finally:
            cleanup_started.set()
            await release_cleanup.wait()

    monkeypatch.setattr(
        harness.llm.primary,
        "_stream_content",
        AsyncMock(side_effect=lambda _: primary()),
    )
    backup = Stream(chunk("backup"))
    monkeypatch.setattr(
        harness.llm.fallback,
        "_stream_content",
        AsyncMock(side_effect=lambda _: backup()),
    )

    async def on_frame(_, frame):
        if isinstance(frame, LLMTextFrame):
            output.set()

    harness.downstream.add_event_handler("on_after_process_frame", on_frame)
    task = asyncio.create_task(generate(harness))
    try:
        await asyncio.wait_for(output.wait(), 0.5)
        await asyncio.wait_for(cleanup_started.wait(), 0.5)
        assert not task.done()
    finally:
        release_cleanup.set()
    assert await task == ["backup"]


@pytest.mark.asyncio
async def test_metadata_trickle_cannot_extend_first_output_deadline(
    harness, monkeypatch
):
    harness.llm._first_output_timeout_secs = 0.06

    async def metadata():
        while True:
            yield GenerateContentResponse()
            await asyncio.sleep(0.005)

    for service in (harness.llm.primary, harness.llm.fallback):
        monkeypatch.setattr(
            service, "_stream_content", AsyncMock(side_effect=lambda _: metadata())
        )
    assert await generate(harness) == []
    assert sum(isinstance(f, ErrorFrame) for f in harness.upstream.frames) == 1


@pytest.mark.asyncio
async def test_disabled_public_metrics_still_arbitrate_tools(harness, monkeypatch):
    harness.llm._setup.enable_metrics = False
    harness.llm._setup.enable_usage_metrics = False
    handler = AsyncMock()
    harness.llm.register_function("book", handler)
    install_streams(
        harness,
        monkeypatch,
        Stream(asyncio.Event()),
        Stream(chunk(parts=[Part(function_call=FunctionCall(name="book", args={}))])),
    )
    await generate(harness)
    await asyncio.sleep(0)
    handler.assert_awaited_once()
    assert harness.llm.fallback_metrics == {"started": 1, "won": 1}
    assert not any(isinstance(f, MetricsFrame) for f in harness.downstream.frames)


@pytest.mark.asyncio
async def test_worker_flush_waits_for_race_and_upstream_continuation(monkeypatch):
    from pipecat.frames.frames import EndFrame
    from pipecat.pipeline.worker import PipelineWorker

    from api.services.pipecat.worker_runner import (
        run_pipeline_worker,
        wait_for_pipeline_worker_started,
    )

    services = [
        GoogleLLMService(
            api_key="test",
            enable_direct_mode=True,
            settings=GoogleLLMService.Settings(model=m),
        )
        for m in (PRIMARY, BACKUP)
    ]
    llm = FallbackLLMProcessor(*services, fallback_after_secs=0.02)
    release = asyncio.Event()
    primary, backup = Stream(asyncio.Event()), Stream(release, chunk("backup"))
    install_streams(SimpleNamespace(llm=llm), monkeypatch, primary, backup)

    class ContinueOnce(Capture):
        def __init__(self):
            super().__init__()
            self.continued = False

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, LLMFullResponseEndFrame) and not self.continued:
                self.continued = True
                primary.steps = (chunk("continuation"),)
                await self.push_frame(
                    LLMContextFrame(LLMContext()), FrameDirection.UPSTREAM
                )

    sink = ContinueOnce()
    worker = PipelineWorker(Pipeline([llm, sink]), enable_rtvi=False)
    running = asyncio.create_task(run_pipeline_worker(worker))
    try:
        await wait_for_pipeline_worker_started(worker, run_task=running)
        await worker.queue_frame(LLMContextFrame(LLMContext()))
        await asyncio.wait_for(backup.started.wait(), 1)
        flush = asyncio.create_task(worker.flush_pipeline(timeout=0.5))
        await asyncio.sleep(0.02)
        assert not flush.done()
        release.set()
        assert await asyncio.wait_for(flush, 2)
        assert [f.text for f in sink.frames if isinstance(f, LLMTextFrame)] == [
            "backup",
            "continuation",
        ]
        assert await worker.flush_pipeline(timeout=0.5)
        await worker.queue_frame(EndFrame())
        await asyncio.wait_for(running, 2)
    finally:
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)


async def control(harness, frame):
    done = asyncio.Event()

    async def completed(*_):
        done.set()

    await harness.llm.queue_frame(frame, callback=completed)
    await asyncio.wait_for(done.wait(), 1)


@pytest.mark.asyncio
async def test_interruption_during_loser_cleanup_waits_before_reusing_services(
    harness, monkeypatch
):
    closing, release = asyncio.Event(), asyncio.Event()

    async def primary():
        try:
            await asyncio.Event().wait()
            yield chunk()
        finally:
            closing.set()
            await release.wait()

    monkeypatch.setattr(
        harness.llm.primary,
        "_stream_content",
        AsyncMock(side_effect=lambda _: primary()),
    )
    monkeypatch.setattr(
        harness.llm.fallback,
        "_stream_content",
        AsyncMock(side_effect=lambda _: Stream(chunk("backup"))()),
    )
    await harness.llm.queue_frame(LLMContextFrame(LLMContext()))
    await asyncio.wait_for(closing.wait(), 1)
    interrupt = asyncio.create_task(control(harness, InterruptionFrame()))
    try:
        await asyncio.sleep(0.01)
        assert not interrupt.done()
    finally:
        release.set()
    await interrupt
    install_streams(
        harness, monkeypatch, Stream(chunk("next")), Stream(chunk("unused"))
    )
    assert (await generate(harness))[-1] == "next"


@pytest.mark.asyncio
async def test_new_context_retires_backup_deferred_transition(harness, monkeypatch):
    from pipecat.frames.frames import BotStoppedSpeakingFrame

    transition = AsyncMock()
    harness.llm.register_function("move", transition, is_node_transition=True)
    primary = Stream(asyncio.Event())
    backup = Stream(
        chunk(
            parts=[
                Part(text="Moving you"),
                Part(function_call=FunctionCall(name="move", args={})),
            ]
        )
    )
    install_streams(harness, monkeypatch, primary, backup)
    assert await generate(harness) == ["Moving you"]
    assert harness.llm.fallback._pending_node_transition_function_calls
    primary.steps = (chunk("new turn"),)
    await generate(harness)
    await control(harness, BotStoppedSpeakingFrame())
    transition.assert_not_awaited()
    assert not harness.llm.fallback._pending_node_transition_function_calls


@pytest.mark.asyncio
async def test_speculative_winner_releases_once_on_confirmed_turn(harness, monkeypatch):
    from pipecat.frames.frames import UserStoppedSpeakingFrame

    install_streams(
        harness, monkeypatch, Stream(asyncio.Event()), Stream(chunk("backup"))
    )
    assert await generate(harness, speculation=True) == []
    await control(harness, UserStoppedSpeakingFrame())
    assert [
        f.text for f in harness.downstream.frames if isinstance(f, LLMTextFrame)
    ] == ["backup"]
    assert (
        sum(isinstance(f, LLMFullResponseStartFrame) for f in harness.downstream.frames)
        == 1
    )
    assert (
        sum(isinstance(f, LLMFullResponseEndFrame) for f in harness.downstream.frames)
        == 1
    )


@pytest.mark.asyncio
async def test_superseded_speculative_backup_cannot_release_old_text(
    harness, monkeypatch
):
    from pipecat.frames.frames import UserStoppedSpeakingFrame

    primary = Stream(asyncio.Event())
    install_streams(harness, monkeypatch, primary, Stream(chunk("old")))
    assert await generate(harness, speculation=True) == []
    primary.steps = (chunk("new"),)
    assert await generate(harness) == ["new"]
    await control(harness, UserStoppedSpeakingFrame())
    assert [
        f.text for f in harness.downstream.frames if isinstance(f, LLMTextFrame)
    ] == ["new"]


@pytest.mark.asyncio
async def test_summary_uses_primary_even_after_backup_won(harness, monkeypatch):
    from pipecat.frames.frames import (
        LLMContextSummaryRequestFrame,
        LLMContextSummaryResultFrame,
    )

    install_streams(
        harness, monkeypatch, Stream(asyncio.Event()), Stream(chunk("backup"))
    )
    await generate(harness)
    monkeypatch.setattr(
        harness.llm.primary, "_generate_summary", AsyncMock(return_value=("summary", 3))
    )
    await control(
        harness,
        LLMContextSummaryRequestFrame(
            request_id="summary",
            context=LLMContext(),
            min_messages_to_keep=1,
            target_context_tokens=100,
            summarization_prompt="summarize",
        ),
    )
    async with asyncio.timeout(1):
        while not any(
            isinstance(f, LLMContextSummaryResultFrame)
            for f in harness.downstream.frames
        ):
            await asyncio.sleep(0.001)
    results = [
        f
        for f in harness.downstream.frames
        if isinstance(f, LLMContextSummaryResultFrame)
    ]
    assert len(results) == 1 and results[0].summary == "summary"


@pytest.mark.asyncio
async def test_only_winner_is_accepted_by_dograh_tool_owner(
    harness, monkeypatch, simple_workflow
):
    from api.services.workflow.pipecat_engine import PipecatEngine

    engine = PipecatEngine(
        llm=harness.llm,
        workflow=simple_workflow,
        context=LLMContext(),
        call_context_vars={},
        workflow_run_id=123,
    )
    saved = []

    async def save(params):
        saved.append(params.tool_call_id)
        await params.result_callback({"saved": True})

    harness.llm.register_function("book", engine.active_agent.bind_tool(engine, save))
    release = asyncio.Event()
    primary = Stream(
        release,
        chunk(
            parts=[Part(function_call=FunctionCall(name="book", args={}, id="primary"))]
        ),
    )
    backup = Stream(
        release,
        chunk(
            parts=[Part(function_call=FunctionCall(name="book", args={}, id="backup"))]
        ),
    )
    install_streams(harness, monkeypatch, primary, backup)
    task = asyncio.create_task(generate(harness))
    await asyncio.wait_for(backup.started.wait(), 1)
    release.set()
    await task
    await engine.active_agent.finish_tool_calls()
    assert len(saved) == 1
    assert len(engine._gathered_context["tool_results"]) == 1
