"""Exercise recovery through Dograh's OpenAI-compatible provider path."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openai.types.chat import ChatCompletionChunk
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    FunctionCallResultProperties,
    InterruptionFrame,
    LLMContextFrame,
    LLMTextFrame,
    LLMThoughtTextFrame,
    StartFrame,
    TTSAudioRawFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_response_universal import LLMAssistantAggregator
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
from pipecat.utils.asyncio.task_manager import TaskManager

from api.services.pipecat.service_factory import (
    DograhLLMService,
    MiniMaxLLMService,
    create_llm_service_from_provider,
)
from api.tests import test_fallback_llm_processor as fallback_tests
from api.tests.test_fallback_llm_processor import Stream, generate
from api.tests.test_gemini_spoken_tool_calls import Capture, MockTTSService, context

harness = fallback_tests.harness


def chunk(text=None, *, tools=None, finish=None, usage=None, **delta):
    return ChatCompletionChunk(
        id="response-id",
        created=1,
        model="offline-model",
        object="chat.completion.chunk",
        choices=[
            {
                "index": 0,
                "delta": {"content": text, "tool_calls": tools, **delta},
                "finish_reason": finish,
            }
        ],
        usage=usage,
    )


def native(name, args="{}", *, index=0, call_id="native"):
    return {
        "index": index,
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


def install_stream(service, steps, finish="stop", *, paused=None):
    closed = asyncio.Event()

    async def stream():
        try:
            for step in steps:
                if isinstance(step, BaseException):
                    raise step
                if isinstance(step, asyncio.Event):
                    if paused is not None:
                        paused.set()
                    await asyncio.wait_for(step.wait(), 2)
                else:
                    yield chunk(step) if isinstance(step, str) else step
            if finish is not None:
                yield chunk(finish=finish)
        finally:
            closed.set()

    service._client.chat.completions.create = AsyncMock(return_value=stream())
    return closed


def instrument(service=None):
    service = service or DograhLLMService(api_key="offline-key")
    frames, calls = [], []
    service._setup = FrameProcessorSetup(
        clock=SystemClock(),
        task_manager=TaskManager(),
        pipeline_worker=SimpleNamespace(app_resources={}, worker_runner=None),
    )
    service.push_error = AsyncMock()

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        frames.append(frame)

    service.push_frame = push

    async def broadcast(cls, **kwargs):
        frames.append(cls(**kwargs))

    service.broadcast_frame = broadcast

    async def handler(params):
        calls.append((params.function_name, params.arguments, params.tool_call_id))
        await params.result_callback(
            {"ok": True}, properties=FunctionCallResultProperties(run_llm=False)
        )

    service.register_function("end_call", handler, is_node_transition=True)
    service.register_function("save", handler)

    async def inline(items):
        for item in items:
            await service._run_function_call(item)

    service._run_parallel_function_calls = inline
    return service, frames, calls


def speech(frames):
    return "".join(f.text for f in frames if isinstance(f, LLMTextFrame)).strip()


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [1, 3, 1000])
async def test_dograh_text_call_executes_once_without_speech(width):
    service, frames, calls = instrument()
    text = "severe:default_api:end_call{}"
    closed = install_stream(
        service, [text[i : i + width] for i in range(0, len(text), width)]
    )
    await service._process_context(context())
    assert [(name, args) for name, args, _ in calls] == [("end_call", {})]
    assert calls[0][2].startswith("text-")
    assert speech(frames) == ""
    assert closed.is_set()
    service.push_error.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("native_first", [True, False])
async def test_fragmented_native_name_wins_over_text_arguments(native_first):
    service, frames, calls = instrument()
    text = '{"action":"save","action_input":{"reason":"text"}}'
    parts = [
        chunk(tools=[native("sa", '{"reason":')]),
        chunk(tools=[native("ve", '"native"}', call_id=None)]),
    ]
    install_stream(
        service, parts + [text] if native_first else [text] + parts, finish="tool_calls"
    )
    await service._process_context(context())
    assert calls == [("save", {"reason": "native"}, "native")]
    assert speech(frames) == ""


@pytest.mark.asyncio
async def test_mixed_native_and_recovered_batch_waits_for_goodbye():
    service, frames, calls = instrument()
    install_stream(
        service,
        [
            "Goodbye! default_api:end_call{}",
            chunk(tools=[native("save", '{"reason":"tomorrow"}')]),
        ],
        finish="tool_calls",
    )
    await service._process_context(context())
    assert calls == []
    assert speech(frames) == "Goodbye!"
    await service._run_pending_node_transition_function_calls(BotStoppedSpeakingFrame())
    assert [(name, args) for name, args, _ in calls] == [
        ("save", {"reason": "tomorrow"}),
        ("end_call", {}),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", [None, "length", "content_filter"])
async def test_incomplete_response_does_not_execute_recovery(finish):
    service, frames, calls = instrument()
    install_stream(service, ["default_api:end_call{}"], finish=finish)
    await service._process_context(context())
    assert not calls
    assert not speech(frames)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "choice, allowed",
    [
        ("none", False),
        ({"type": "function", "function": {"name": "save"}}, False),
        ({"type": "function", "function": {"name": "end_call"}}, True),
    ],
)
async def test_recovery_respects_explicit_tool_choice(choice, allowed):
    service, frames, calls = instrument()
    ctx = context()
    ctx.set_tool_choice(choice)
    install_stream(service, ["default_api:end_call{}"])
    await service._process_context(ctx)
    assert bool(calls) == allowed
    assert not speech(frames)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [RuntimeError("stream broke"), asyncio.CancelledError()]
)
async def test_stream_failure_closes_stream_and_does_not_reuse_recovery(error):
    service, frames, calls = instrument()
    closed = install_stream(service, ["default_api:end_call{}", error])
    with pytest.raises(type(error)):
        await service._process_context(context())
    assert closed.is_set()
    install_stream(service, ["Hello again."])
    await service._process_context(context())
    assert not calls
    assert speech(frames) == "Hello again."


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["node", "interruption", "generation"])
async def test_stale_response_cannot_recover(change):
    service, frames, calls = instrument()
    ctx = context()
    release, paused = asyncio.Event(), asyncio.Event()
    install_stream(service, ["default_api:end_call{}", release], paused=paused)
    task = asyncio.create_task(service._process_context(ctx))
    await asyncio.wait_for(paused.wait(), 1)
    if change == "node":
        ctx.set_tools(ToolsSchema(standard_tools=[]))
    elif change == "interruption":
        service._interrupt_tool_response(InterruptionFrame())
    else:
        service._begin_tool_response()
    release.set()
    await asyncio.wait_for(task, 2)
    assert not calls
    assert not speech(frames)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider, model",
    [
        ("dograh", "default"),
        ("openai", "gpt-4o"),
        ("atlascloud", "offline"),
        ("hopper", "offline"),
        ("groq", "offline"),
        ("openrouter", "offline"),
        ("azure", "gpt-4o"),
        ("speaches", "offline"),
        ("huggingface", "offline"),
        ("minimax", "MiniMax-M2.7"),
        ("sarvam", "sarvam-105b"),
    ],
)
async def test_factory_openai_compatible_providers_recover(provider, model):
    service = create_llm_service_from_provider(
        provider,
        model,
        "offline-key",
        endpoint="https://example.com",
        base_url="https://example.com/v1",
    )
    service, frames, calls = instrument(service)
    install_stream(service, list("default_api:end_call{}"))
    await service._process_context(context())
    assert [(name, args) for name, args, _ in calls] == [("end_call", {})]
    assert not speech(frames)
    service.push_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_minimax_thinking_is_filtered_before_tool_recovery():
    service, frames, calls = instrument(MiniMaxLLMService(api_key="offline-key"))
    install_stream(
        service,
        list(
            '<think>default_api:save{"reason":"private"}</think>default_api:end_call{}'
        ),
    )
    await service._process_context(context())
    assert [(name, args) for name, args, _ in calls] == [("end_call", {})]
    assert not speech(frames)
    assert any(isinstance(f, LLMThoughtTextFrame) for f in frames)


@pytest.mark.asyncio
async def test_speech_in_same_chunk_as_native_call_still_plays_before_transition():
    service, frames, calls = instrument()
    install_stream(
        service, [chunk("Goodbye! default_api:end_call{}", tools=[native("end_call")])]
    )
    await service._process_context(context())
    assert speech(frames) == "Goodbye!"
    assert not calls
    await service._run_pending_node_transition_function_calls(BotStoppedSpeakingFrame())
    assert calls == [("end_call", {}, "native")]


@pytest.mark.asyncio
async def test_multiple_native_calls_in_one_chunk_are_preserved():
    service, frames, calls = instrument()
    install_stream(
        service,
        [
            "default_api:end_call{}",
            chunk(
                tools=[
                    native("save", '{"reason":"native"}'),
                    native("end_call", index=1, call_id="end"),
                ]
            ),
        ],
        finish="tool_calls",
    )
    await service._process_context(context())
    assert calls == [("save", {"reason": "native"}, "native"), ("end_call", {}, "end")]
    assert not speech(frames)


@pytest.mark.asyncio
async def test_interleaved_native_arguments_do_not_drop_calls_or_duplicate_recovery():
    service, frames, calls = instrument()
    install_stream(
        service,
        [
            chunk(
                tools=[
                    native("save", ""),
                    native("end_call", "", index=1, call_id="end"),
                ]
            ),
            chunk(
                tools=[
                    native(None, '{"reason":"tomorrow"}', call_id=None),
                    native(None, "{}", index=1, call_id=None),
                ]
            ),
            "default_api:end_call{}",
        ],
        finish="tool_calls",
    )
    await service._process_context(context())
    assert calls == [
        ("save", {"reason": "tomorrow"}, "native"),
        ("end_call", {}, "end"),
    ]
    assert not speech(frames)


@pytest.mark.asyncio
async def test_dograh_request_metadata_and_usage_survive_recovery():
    service, frames, calls = instrument(
        DograhLLMService(
            api_key="offline-key",
            correlation_id="corr-123",
            usage_context="voice-call",
        )
    )
    usage_chunk = chunk(
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    )
    usage_chunk.choices = []
    install_stream(
        service,
        ["default_api:end_call{}", chunk(finish="stop"), usage_chunk],
        finish=None,
    )
    service.start_llm_usage_metrics = AsyncMock()
    await service._process_context(context())
    params = service._client.chat.completions.create.call_args.kwargs
    assert params["metadata"]["correlation_id"] == "corr-123"
    assert params["metadata"]["usage_context"] == "voice-call"
    service.start_llm_usage_metrics.assert_awaited_once()
    assert service.start_llm_usage_metrics.call_args.args[0].total_tokens == 15
    assert calls[0][0] == "end_call"
    assert not speech(frames)


@pytest.mark.asyncio
async def test_refusal_does_not_recover_tool():
    service, frames, calls = instrument()
    install_stream(service, ["default_api:end_call{}", chunk(refusal="Cannot comply")])
    await service._process_context(context())
    assert not calls
    assert not speech(frames)


@pytest.mark.asyncio
async def test_cancellation_closes_http_response_and_iterator():
    service, _frames, calls = instrument()
    started, closed = asyncio.Event(), asyncio.Event()

    async def chunks():
        try:
            yield chunk("default_api:end_call{}")
            started.set()
            await asyncio.wait_for(asyncio.Event().wait(), 2)
        finally:
            closed.set()

    class HTTPStream:
        close = AsyncMock()

        def __aiter__(self):
            return chunks()

    stream = HTTPStream()
    service._client.chat.completions.create = AsyncMock(return_value=stream)
    task = asyncio.create_task(service._process_context(context()))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert closed.is_set()
    stream.close.assert_awaited_once()
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("harness", [DograhLLMService], indirect=True)
async def test_dograh_fallback_only_winner_executes_recovered_tool(harness):
    saved = []

    async def save(params):
        saved.append(params.arguments)
        await params.result_callback(
            {"ok": True}, properties=FunctionCallResultProperties(run_llm=False)
        )

    harness.llm.register_function("save", save)
    release = asyncio.Event()
    streams = [
        Stream(
            release,
            chunk('{"action":"save","action_input":{"reason":"' + label + '"}}'),
            chunk(finish="stop"),
        )
        for label in ("primary", "backup")
    ]
    for service, stream in zip(harness.llm._services, streams):
        service._client.chat.completions.create = AsyncMock(return_value=stream())
    task = asyncio.create_task(generate(harness, context=context()))
    await asyncio.wait_for(streams[1].started.wait(), 1)
    release.set()
    texts = await asyncio.wait_for(task, 2)
    async with asyncio.timeout(1):
        while not saved:
            await asyncio.sleep(0.01)
    assert len(saved) == 1
    assert not "".join(texts).strip()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text, expected_speech, name, args",
    [
        ("Goodbye! default_api:end_call{}", ["Goodbye!"], "end_call", {}),
        (
            '{"action":"save","action_input":{"reason":"Busy today. Call tomorrow."}}',
            [],
            "save",
            {"reason": "Busy today. Call tomorrow."},
        ),
    ],
)
async def test_dograh_tts_and_assistant_context(text, expected_speech, name, args):
    service = DograhLLMService(api_key="offline-key")
    ctx = context()
    called = asyncio.Event()

    async def handler(params):
        assert params.arguments == args
        called.set()
        await params.result_callback(
            {"ok": True}, properties=FunctionCallResultProperties(run_llm=False)
        )

    service.register_function(name, handler, is_node_transition=name == "end_call")
    tts = MockTTSService(
        mock_audio_duration_ms=20, frame_delay=0, pause_frame_processing=False
    )
    sink = Capture()
    pipeline = Pipeline([service, tts, sink, LLMAssistantAggregator(ctx)])
    manager = TaskManager()
    await pipeline.setup(
        FrameProcessorSetup(
            clock=SystemClock(),
            task_manager=manager,
            pipeline_worker=SimpleNamespace(app_resources={}, worker_runner=None),
        )
    )
    try:
        await pipeline.queue_frame(StartFrame())
        await asyncio.wait_for(sink.started.wait(), 2)
        install_stream(service, list(text))
        await service.process_frame(LLMContextFrame(ctx), FrameDirection.DOWNSTREAM)
        await asyncio.wait_for(sink.ended.wait(), 2)
        assert [t.strip() for t in tts.received_texts] == expected_speech
        assert any(isinstance(f, TTSAudioRawFrame) for f in sink.frames) == bool(
            expected_speech
        )
        if expected_speech:
            assert not called.is_set()
            await service.process_frame(
                BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM
            )
        await asyncio.wait_for(called.wait(), 2)
        async with asyncio.timeout(2):
            while not any(m.get("role") == "tool" for m in ctx.messages):
                await asyncio.sleep(0.01)
        serialized = json.dumps(ctx.messages)
        assert "default_api" not in serialized
        assert "text-" in serialized
        assert all(t in serialized for t in expected_speech)
    finally:
        await pipeline.cleanup()
    assert not manager.current_tasks()
