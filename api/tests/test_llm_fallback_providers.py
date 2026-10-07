"""Real Pipecat services with deterministic streams; no provider network calls."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AuthenticationError, RateLimitError
from openai.types.chat import ChatCompletionChunk
from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import ErrorFrame, StartFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameProcessorSetup
from pipecat.services.settings import LLMSettings
from pipecat.utils.asyncio.task_manager import TaskManager

from api.schemas.llm_fallback import ErrorCondition, NoOutputCondition
from api.services.pipecat.fallback_llm import FallbackLLMProcessor, FallbackRoute
from api.services.pipecat.service_factory import create_llm_service_from_provider
from api.tests.test_fallback_llm_processor import Capture, Stream, chunk, generate


def openai_chunk(content=None, *, tools=None, role=None):
    return ChatCompletionChunk.model_validate(
        {
            "id": "response",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gpt-4.1",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": content, "tool_calls": tools, "role": role},
                    "finish_reason": None,
                }
            ],
        }
    )


@asynccontextmanager
async def rig(
    monkeypatch,
    providers=("openai", "google"),
    triggers=(("no_output", 1), ("error", 1)),
):
    models = {
        "openai": "gpt-4.1",
        "google": "gemini-3.5-flash",
        "aws_bedrock": "anthropic.claude-3-5-sonnet-20241022-v2:0",
    }
    services = [
        create_llm_service_from_provider(
            provider,
            models[provider],
            "test",
            enable_direct_mode=True,
            aws_access_key="test",
            aws_secret_key="test",
            aws_region="us-east-1",
        )
        for provider in providers
    ]
    routes = [
        FallbackRoute(
            NoOutputCondition.model_construct(after_ms=20)
            if trigger == "no_output"
            else ErrorCondition(),
            services[index],
        )
        for trigger, index in triggers
    ]
    llm = FallbackLLMProcessor(
        services[0], routes=routes, first_output_timeout_secs=0.3
    )
    upstream, downstream = Capture(), Capture()
    pipeline = Pipeline([upstream, llm, downstream])
    manager = TaskManager()
    await pipeline.setup(
        FrameProcessorSetup(
            clock=SystemClock(),
            task_manager=manager,
            pipeline_worker=SimpleNamespace(app_resources={}, worker_runner=None),
            enable_metrics=False,
            enable_usage_metrics=False,
        )
    )
    await pipeline.queue_frame(StartFrame())
    await asyncio.wait_for(downstream.started.wait(), 1)

    def install(index, stream):
        service = services[index]
        if providers[index] == "openai":
            monkeypatch.setattr(
                service,
                "get_chat_completions",
                AsyncMock(side_effect=lambda _: stream()),
            )
        elif providers[index] == "google":
            monkeypatch.setattr(
                service, "_stream_content", AsyncMock(side_effect=lambda _: stream())
            )
        else:

            @asynccontextmanager
            async def client(**_):
                yield SimpleNamespace(
                    converse_stream=AsyncMock(return_value={"stream": stream()})
                )

            monkeypatch.setattr(
                service, "_aws_session", SimpleNamespace(create_client=client)
            )

    try:
        yield SimpleNamespace(
            llm=llm,
            services=services,
            install=install,
            upstream=upstream,
            downstream=downstream,
        )
    finally:
        await pipeline.cleanup()
        assert not manager.current_tasks()


@pytest.mark.asyncio
async def test_openai_role_metadata_does_not_win_or_disable_slow_fallback(monkeypatch):
    async with rig(monkeypatch) as h:
        primary = Stream(openai_chunk(role="assistant"), asyncio.Event())
        backup = Stream(chunk("Gemini answer"))
        h.install(0, primary)
        h.install(1, backup)
        assert await generate(h) == ["Gemini answer"]
        assert primary.closed.is_set() and backup.closed.is_set()
        assert h.llm.fallback_metrics == {"started": 1, "won": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [None, 429, 401, 402])
async def test_empty_or_provider_error_starts_error_target_without_slow_rule(
    monkeypatch, error
):
    async with rig(monkeypatch, triggers=(("error", 1),)) as h:
        failure = openai_chunk(role="assistant")
        if error:
            response = httpx.Response(
                error, request=httpx.Request("POST", "https://example.test")
            )
            cls = RateLimitError if error == 429 else AuthenticationError
            failure = cls("request failed", response=response, body={})
        h.install(0, Stream(failure))
        h.install(1, Stream(chunk("recovered")))
        assert await generate(h) == ["recovered"]
        assert not any(isinstance(frame, ErrorFrame) for frame in h.upstream.frames)


@pytest.mark.asyncio
async def test_slow_rule_does_not_enable_error_failover(monkeypatch):
    async with rig(monkeypatch, triggers=(("no_output", 1),)) as h:
        backup = Stream(chunk("unexpected"))
        h.install(0, Stream(RuntimeError("billing failure")))
        h.install(1, backup)
        assert await generate(h) == []
        assert not backup.started.is_set()
        assert sum(isinstance(frame, ErrorFrame) for frame in h.upstream.frames) == 1


@pytest.mark.asyncio
async def test_error_rule_does_not_enable_slow_racing(monkeypatch):
    async with rig(monkeypatch, triggers=(("error", 1),)) as h:
        release = asyncio.Event()
        primary, backup = (
            Stream(release, openai_chunk("primary")),
            Stream(chunk("unexpected")),
        )
        h.install(0, primary)
        h.install(1, backup)
        task = asyncio.create_task(generate(h))
        await primary.started.wait()
        await asyncio.sleep(0.04)
        assert not backup.started.is_set()
        release.set()
        assert await task == ["primary"]


@pytest.mark.asyncio
async def test_error_target_recovers_failed_slow_target_and_cancels_primary(
    monkeypatch,
):
    async with rig(
        monkeypatch,
        providers=("openai", "google", "openai"),
        triggers=(("no_output", 1), ("error", 2)),
    ) as h:
        primary = Stream(asyncio.Event())
        h.install(0, primary)
        h.install(1, Stream(RuntimeError("quota exhausted")))
        h.install(2, Stream(openai_chunk("third connection")))
        assert await generate(h) == ["third connection"]
        assert primary.closed.is_set()
        assert h.llm.fallback_metrics == {"started": 2, "won": 1}


@pytest.mark.asyncio
async def test_same_target_is_never_retried_after_it_fails(monkeypatch):
    async with rig(monkeypatch) as h:
        release = asyncio.Event()
        h.install(0, Stream(release, RuntimeError("primary failure")))
        backup = Stream(RuntimeError("fallback failure"))
        h.install(1, backup)
        task = asyncio.create_task(generate(h))
        await backup.closed.wait()
        release.set()
        assert await task == []
        assert h.llm.fallback_metrics == {"started": 1, "won": 0}
        assert sum(isinstance(frame, ErrorFrame) for frame in h.upstream.frames) == 1


@pytest.mark.asyncio
async def test_no_switch_after_openai_text_even_if_stream_errors(monkeypatch):
    async with rig(monkeypatch) as h:
        h.install(
            0, Stream(openai_chunk("partial answer"), RuntimeError("lost stream"))
        )
        backup = Stream(chunk("unexpected"))
        h.install(1, backup)
        assert await generate(h) == ["partial answer"]
        assert not backup.started.is_set()
        assert sum(isinstance(frame, ErrorFrame) for frame in h.upstream.frames) == 1


@pytest.mark.asyncio
async def test_openai_tool_only_wins_and_loser_cannot_execute_tools(monkeypatch):
    async with rig(monkeypatch, providers=("google", "openai")) as h:
        from google.genai.types import FunctionCall, Part

        release = asyncio.Event()
        primary = Stream(
            release,
            chunk(parts=[Part(function_call=FunctionCall(name="book", args={}))]),
        )
        backup = Stream(
            release,
            openai_chunk(
                tools=[
                    {
                        "index": 0,
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "book", "arguments": "{}"},
                    }
                ]
            ),
        )
        h.install(0, primary)
        h.install(1, backup)
        handler, prepared = AsyncMock(), AsyncMock()
        h.llm.register_function("book", handler)
        h.llm.add_event_handler("on_function_calls_prepared", prepared)
        task = asyncio.create_task(generate(h))
        await backup.started.wait()
        release.set()
        await task
        await asyncio.sleep(0)
        prepared.assert_awaited_once()
        handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_bedrock_tool_boundary_wins_without_gemini_info_frame(monkeypatch):
    async with rig(monkeypatch, providers=("openai", "aws_bedrock")) as h:
        h.install(0, Stream(asyncio.Event()))
        h.install(
            1,
            Stream(
                {"messageStart": {"role": "assistant"}},
                {
                    "contentBlockStart": {
                        "contentBlockIndex": 0,
                        "start": {"toolUse": {"toolUseId": "tool-1", "name": "book"}},
                    }
                },
                {
                    "contentBlockDelta": {
                        "contentBlockIndex": 0,
                        "delta": {"toolUse": {"input": "{}"}},
                    }
                },
                {"contentBlockStop": {"contentBlockIndex": 0}},
            ),
        )
        from pipecat.adapters.schemas.function_schema import FunctionSchema
        from pipecat.adapters.schemas.tools_schema import ToolsSchema

        context = LLMContext(
            messages=[{"role": "user", "content": "book"}],
            tools=ToolsSchema(
                standard_tools=[
                    FunctionSchema(
                        name="book", description="Book", properties={}, required=[]
                    )
                ]
            ),
        )
        handler, prepared = AsyncMock(), AsyncMock()
        h.llm.register_function("book", handler, is_node_transition=True)
        h.llm.add_event_handler("on_function_calls_prepared", prepared)
        assert await generate(h, context=context) == []
        await asyncio.sleep(0)
        prepared.assert_awaited_once()
        handler.assert_awaited_once()
        assert h.llm.fallback_metrics["won"] == 1


@pytest.mark.asyncio
async def test_instructions_cross_providers_without_copying_sampling_or_model(
    monkeypatch,
):
    async with rig(monkeypatch) as h:
        old = h.services[1]._settings.temperature
        await h.llm._update_settings(
            LLMSettings(
                system_instruction="new prompt", temperature=0.9, model="other-primary"
            )
        )
        assert h.services[1]._settings.system_instruction == "new prompt"
        assert h.services[1]._settings.temperature == old
        assert h.services[1]._settings.model == "gemini-3.5-flash"


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["error", "no_output"])
async def test_one_shot_inference_fallback_before_pipeline_setup(monkeypatch, trigger):
    # Use real LLM services: one-shot consumers never set up a frame pipeline.
    primary = create_llm_service_from_provider(
        "openai", "gpt-4.1", "test", enable_direct_mode=True
    )
    backup = create_llm_service_from_provider(
        "google", "gemini-3.5-flash", "test", enable_direct_mode=True
    )
    closed = asyncio.Event()

    async def failure(*args, **kwargs):
        try:
            if trigger == "error":
                raise RuntimeError("billing failed")
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(primary, "run_inference", failure)
    infer = AsyncMock(return_value='{"answer": "recovered"}')
    monkeypatch.setattr(backup, "run_inference", infer)
    condition = (
        ErrorCondition()
        if trigger == "error"
        else NoOutputCondition.model_construct(after_ms=20)
    )
    llm = FallbackLLMProcessor(primary, routes=[FallbackRoute(condition, backup)])
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    try:
        assert (
            await llm.run_inference(LLMContext(), response_schema=schema)
            == '{"answer": "recovered"}'
        )
        assert infer.call_args.kwargs["response_schema"] == schema
        assert closed.is_set()
        assert llm.fallback_metrics == {"started": 1, "won": 1}
    finally:
        await llm.cleanup()


@pytest.mark.asyncio
async def test_one_shot_cancellation_joins_all_requests(monkeypatch):
    async with rig(monkeypatch) as h:
        started = [asyncio.Event(), asyncio.Event()]
        closed = [asyncio.Event(), asyncio.Event()]

        async def stalled(index, *args, **kwargs):
            started[index].set()
            try:
                await asyncio.Event().wait()
            finally:
                closed[index].set()

        from functools import partial

        for index, service in enumerate(h.services):
            monkeypatch.setattr(service, "run_inference", partial(stalled, index))
        task = asyncio.create_task(h.llm.run_inference(LLMContext()))
        await started[1].wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert all(event.is_set() for event in closed)
