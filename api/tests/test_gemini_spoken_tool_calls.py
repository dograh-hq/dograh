"""Exercise recovery through Gemini's parser, tool dispatcher and TTS pipeline."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from google.genai.types import (
    Candidate,
    Content,
    FinishReason,
    FunctionCall,
    GenerateContentResponse,
    Part,
)
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    FunctionCallResultProperties,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
    StartFrame,
    TTSAudioRawFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMAssistantAggregator
from pipecat.processors.frame_processor import (
    FrameDirection,
    FrameProcessor,
    FrameProcessorSetup,
)
from pipecat.tests.mock_tts_service import MockTTSService
from pipecat.utils.asyncio.task_manager import TaskManager

from api.services.pipecat.gemini_spoken_tool_calls import GeminiSpokenToolCallMixin
from api.services.pipecat.service_factory import (
    DograhGoogleLLMService,
    DograhGoogleVertexLLMService,
)
from api.tests import test_fallback_llm_processor as fallback_tests
from api.tests.test_fallback_llm_processor import Stream, generate, install_streams

harness = fallback_tests.harness


GuardedGoogle = DograhGoogleLLMService


def test_both_gemini_provider_paths_enable_recovery():
    assert issubclass(DograhGoogleLLMService, GeminiSpokenToolCallMixin)
    assert issubclass(DograhGoogleVertexLLMService, GeminiSpokenToolCallMixin)


def chunk(*parts, finish=None):
    return GenerateContentResponse(
        candidates=[
            Candidate(
                content=Content(role="model", parts=list(parts)),
                finish_reason=finish,
            )
        ]
    )


def install_stream(service, steps, finish=FinishReason.STOP, *, paused=None):
    async def stream(_):
        async def generate():
            for step in steps:
                if isinstance(step, BaseException):
                    raise step
                if isinstance(step, asyncio.Event):
                    if paused is not None:
                        paused.set()
                    await asyncio.wait_for(step.wait(), 2)
                else:
                    yield chunk(Part(text=step)) if isinstance(step, str) else step
            if finish is not None:
                yield chunk(finish=finish)

        return generate()

    service._stream_content = stream


def context():
    return LLMContext(
        tools=ToolsSchema(
            standard_tools=[
                FunctionSchema("end_call", "Finish the call", {}, []),
                FunctionSchema(
                    "save",
                    "Save a callback",
                    {"reason": {"type": "string"}},
                    ["reason"],
                ),
            ]
        )
    )


def instrument():
    service = GuardedGoogle(api_key="offline-key")
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


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [1, 3, 1000])
async def test_text_only_call_runs_once_and_is_not_spoken(width):
    service, frames, calls = instrument()
    text = "severe:default_api:end_call{}"
    install_stream(service, [text[i : i + width] for i in range(0, len(text), width)])
    await service._process_context(context())
    service.push_error.assert_not_awaited()
    assert [(name, args) for name, args, _ in calls] == [("end_call", {})]
    assert calls[0][2].startswith("text-")
    assert not [f for f in frames if isinstance(f, LLMTextFrame) and f.text.strip()]


@pytest.mark.asyncio
@pytest.mark.parametrize("native_first", [True, False])
async def test_native_call_wins_over_text_duplicate(native_first):
    service, _frames, calls = instrument()
    native = chunk(
        Part(function_call=FunctionCall(id="native", name="end_call", args={}))
    )
    text = "default_api:end_call{}"
    install_stream(service, [native, text] if native_first else [text, native])
    await service._process_context(context())
    assert calls == [("end_call", {}, "native")]


@pytest.mark.asyncio
async def test_goodbye_defers_end_call_until_playback():
    service, frames, calls = instrument()
    install_stream(service, list("Buona giornata! severe:default_api:end_call{}"))
    await service._process_context(context())
    assert not calls
    assert (
        "".join(f.text for f in frames if isinstance(f, LLMTextFrame)).strip()
        == "Buona giornata!"
    )
    await service._run_pending_node_transition_function_calls(BotStoppedSpeakingFrame())
    assert len(calls) == 1
    await service._run_pending_node_transition_function_calls(BotStoppedSpeakingFrame())
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_recovered_save_does_not_make_native_end_cut_off_goodbye():
    service, _frames, calls = instrument()
    install_stream(
        service,
        [
            'Buona giornata! {"action":"save","action_input":{"reason":"tomorrow"}}',
            chunk(
                Part(function_call=FunctionCall(id="native", name="end_call", args={}))
            ),
        ],
    )
    await service._process_context(context())
    assert not calls
    await service._run_pending_node_transition_function_calls(BotStoppedSpeakingFrame())
    assert [c[0] for c in calls] == ["save", "end_call"]


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", [FinishReason.MAX_TOKENS, FinishReason.SAFETY, None])
async def test_incomplete_response_is_suppressed_but_not_executed(finish):
    service, frames, calls = instrument()
    install_stream(service, ["default_api:end_call{}"], finish=finish)
    await service._process_context(context())
    assert not calls
    assert not any(isinstance(f, LLMTextFrame) and f.text.strip() for f in frames)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [RuntimeError("test stream failed"), asyncio.CancelledError()]
)
async def test_failed_or_cancelled_stream_never_recovers_tools(error):
    service, frames, calls = instrument()
    install_stream(service, ["default_api:end_call{}", error])
    if isinstance(error, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await service._process_context(context())
        service.push_error.assert_not_awaited()
    else:
        # Gemini catches ordinary stream exceptions and reports an ErrorFrame;
        # cancellation must still propagate. Exercise both provider contracts.
        await service._process_context(context())
        service.push_error.assert_awaited_once()
        assert service.push_error.call_args.kwargs["exception"] is error
    assert not calls
    install_stream(service, ["Hello again."])
    await service._process_context(context())
    assert not calls
    assert "Hello again." in "".join(
        f.text for f in frames if isinstance(f, LLMTextFrame)
    )


@pytest.mark.asyncio
async def test_previous_node_registration_is_not_enough_to_recover():
    service, frames, calls = instrument()
    ctx = context()
    ctx.set_tools(ToolsSchema(standard_tools=[]))
    install_stream(service, ["default_api:end_call{}"])
    await service._process_context(ctx)
    assert not calls
    assert not any(isinstance(f, LLMTextFrame) and f.text.strip() for f in frames)


@pytest.mark.asyncio
async def test_explicitly_disabled_tools_cannot_be_recovered():
    service, _frames, calls = instrument()
    ctx = context()
    ctx.set_tool_choice("none")
    install_stream(service, ["default_api:end_call{}"])
    await service._process_context(ctx)
    assert not calls


@pytest.mark.asyncio
async def test_multiple_candidates_do_not_combine_into_a_recovered_action():
    service, frames, calls = instrument()
    response = chunk(Part(text="Hello."))
    response.candidates.append(chunk(Part(text="default_api:end_call{}")).candidates[0])
    install_stream(service, [response])
    await service._process_context(context())
    await service._run_pending_node_transition_function_calls(BotStoppedSpeakingFrame())
    assert not calls
    assert "default_api" not in "".join(
        f.text for f in frames if isinstance(f, LLMTextFrame)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted_during_stream", [False, True])
async def test_interruption_discards_recovered_calls(interrupted_during_stream):
    service, _frames, calls = instrument()
    release, paused = asyncio.Event(), asyncio.Event()
    install_stream(
        service, ["Buona giornata! default_api:end_call{}", release], paused=paused
    )
    task = asyncio.create_task(service._process_context(context()))
    if interrupted_during_stream:
        await asyncio.wait_for(paused.wait(), 1)
        service._interrupt_tool_response(InterruptionFrame())
        release.set()
        await asyncio.wait_for(task, 2)
    else:
        release.set()
        await asyncio.wait_for(task, 2)
        service._interrupt_tool_response(InterruptionFrame())
    await service._run_pending_node_transition_function_calls(BotStoppedSpeakingFrame())
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
async def test_identical_text_calls_are_deduplicated_and_native_arguments_win(native):
    service, _frames, calls = instrument()
    fragment = '{"action":"save","action_input":{"reason":"tomorrow"}}'
    steps = [fragment, fragment]
    if native:
        steps.append(
            chunk(
                Part(
                    function_call=FunctionCall(
                        name="save", id="native", args={"reason": "today"}
                    )
                )
            )
        )
    install_stream(service, steps)
    await service._process_context(context())
    assert len(calls) == 1
    assert calls[0][1] == {"reason": "today" if native else "tomorrow"}


@pytest.mark.asyncio
async def test_conflicting_text_calls_are_not_guessed():
    service, _frames, calls = instrument()
    install_stream(
        service,
        [
            '{"action":"save","action_input":{"reason":"tomorrow"}}',
            '{"action":"save","action_input":{"reason":"today"}}',
        ],
    )
    await service._process_context(context())
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("native_end", [False, True])
async def test_recovery_does_not_introduce_conflicting_transitions(native_end):
    service, _frames, calls = instrument()
    transfer = AsyncMock()
    service.register_function("transfer", transfer, is_node_transition=True)
    ctx = context()
    ctx.set_tools(
        ToolsSchema(
            standard_tools=[
                *ctx.tools.standard_tools,
                FunctionSchema("transfer", "Transfer the call", {}, []),
            ]
        )
    )
    end = (
        chunk(Part(function_call=FunctionCall(name="end_call", id="native", args={})))
        if native_end
        else "default_api:end_call{}"
    )
    install_stream(service, [end, "default_api:transfer{}"])
    await service._process_context(ctx)
    transfer.assert_not_awaited()
    assert calls == ([("end_call", {}, "native")] if native_end else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("harness", [GuardedGoogle], indirect=True)
async def test_only_fallback_winner_can_execute_a_recovered_tool(harness, monkeypatch):
    saved = []

    async def save(params):
        saved.append(params.arguments)
        await params.result_callback(
            {"ok": True}, properties=FunctionCallResultProperties(run_llm=False)
        )

    harness.llm.register_function("save", save)
    release = asyncio.Event()
    primary = Stream(
        release,
        chunk(Part(text='{"action":"save","action_input":{"reason":"primary"}}')),
        chunk(finish=FinishReason.STOP),
    )
    backup = Stream(
        release,
        chunk(Part(text='{"action":"save","action_input":{"reason":"backup"}}')),
        chunk(finish=FinishReason.STOP),
    )
    install_streams(harness, monkeypatch, primary, backup)
    task = asyncio.create_task(generate(harness, context=context()))
    await asyncio.wait_for(backup.started.wait(), 1)
    release.set()
    texts = await asyncio.wait_for(task, 2)
    async with asyncio.timeout(1):
        while not saved:
            await asyncio.sleep(0.01)
    assert len(saved) == 1
    assert not "".join(texts).strip()


@pytest.mark.asyncio
async def test_text_signature_bookmarks_the_clean_speech():
    service, frames, _calls = instrument()
    install_stream(
        service,
        [
            chunk(
                Part(
                    text='Capisco. {"thought":""} La richiamo domani.',
                    thought_signature=b"signature",
                )
            )
        ],
    )
    await service._process_context(context())
    clean = "".join(f.text for f in frames if isinstance(f, LLMTextFrame))
    metadata = [
        m.message
        for f in frames
        if isinstance(f, LLMMessagesAppendFrame)
        for m in f.messages
    ]
    assert metadata[0]["bookmark"]["text"] == clean
    assert "thought" not in clean


class Capture(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.frames = []
        self.started, self.ended = asyncio.Event(), asyncio.Event()

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        self.frames.append(frame)
        if isinstance(frame, StartFrame):
            self.started.set()
        if isinstance(frame, LLMFullResponseEndFrame):
            self.ended.set()
        await self.push_frame(frame, direction)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response, tool_name, expected_speech, expected_args",
    [
        (
            "Buona giornata! severe:default_api:end_call{}",
            "end_call",
            ["Buona giornata!"],
            {},
        ),
        (
            '{"action":"save","action_input":{"reason":"Busy today. Call tomorrow."}}',
            "save",
            [],
            {"reason": "Busy today. Call tomorrow."},
        ),
    ],
)
async def test_real_tts_receives_only_speech_and_context_records_recovered_call(
    response, tool_name, expected_speech, expected_args
):
    service = GuardedGoogle(api_key="offline-key")
    ctx = context()
    called = asyncio.Event()

    async def end(params):
        assert params.function_name == tool_name
        assert params.arguments == expected_args
        called.set()
        await params.result_callback(
            {"ended": True}, properties=FunctionCallResultProperties(run_llm=False)
        )

    service.register_function(
        tool_name, end, is_node_transition=tool_name == "end_call"
    )
    tts = MockTTSService(
        mock_audio_duration_ms=20, frame_delay=0, pause_frame_processing=False
    )
    assistant = LLMAssistantAggregator(ctx)
    sink = Capture()
    pipeline = Pipeline([service, tts, sink, assistant])
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
        install_stream(service, list(response))
        await service._process_context(ctx)
        await asyncio.wait_for(sink.ended.wait(), 2)
        assert [text.strip() for text in tts.received_texts] == expected_speech
        assert any(
            isinstance(frame, TTSAudioRawFrame) for frame in sink.frames
        ) == bool(expected_speech)
        if expected_speech:
            assert not called.is_set()
            await service.process_frame(
                BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM
            )
        await asyncio.wait_for(called.wait(), 2)
        async with asyncio.timeout(2):
            while not any(m.get("role") == "tool" for m in ctx.messages):
                await asyncio.sleep(0.01)
        serialized = json.dumps(ctx.messages, default=str)
        assert "default_api" not in serialized
        for text in expected_speech:
            assert text in serialized
        assert "text-" in serialized
    finally:
        await pipeline.cleanup()
    assert not manager.current_tasks()
