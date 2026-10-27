"""Synthetic speech artifacts must not reappear in transcripts or LLM history."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import (
    AggregatedTextFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    StartFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    TTSTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMAssistantAggregator
from pipecat.processors.frame_processor import FrameProcessor, FrameProcessorSetup
from pipecat.services.tts_service import TextAggregationMode, TTSService, TTSSettings
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.utils.asyncio.task_manager import TaskManager
from pipecat.utils.text.base_text_aggregator import AggregationType
from pipecat.utils.text.base_text_filter import BaseTextFilter
from pipecat.utils.text.spoken_text_filter import SpokenTextFilter

from api.services.pipecat.in_memory_buffers import InMemoryLogsBuffer
from api.services.pipecat.realtime_feedback_observer import (
    RealtimeFeedbackObserver,
    register_turn_log_handlers,
)
from api.services.pipecat.spoken_tool_call_guard import SpokenToolCallGuard
from api.services.pipecat.transcript_log_coordinator import TranscriptLogCoordinator


class OfflineTTS(TTSService):
    def __init__(self, mode, token_streaming):
        super().__init__(
            push_start_frame=True,
            push_stop_frames=True,
            push_text_frames=mode == "sentence",
            pause_frame_processing=False,
            sample_rate=16000,
            settings=TTSSettings(model=None, voice=None, language=None),
            text_aggregation_mode=(
                TextAggregationMode.TOKEN
                if token_streaming
                else TextAggregationMode.SENTENCE
            ),
            text_filters=[SpokenTextFilter()],
        )
        self.mode = mode
        self.requests = []

    async def run_tts(self, text, context_id):
        self.requests.append(text)
        if self.mode == "words":
            await self.add_word_timestamps(
                [(word, i * 0.01) for i, word in enumerate(text.split())],
                context_id=context_id,
            )
        yield TTSAudioRawFrame(b"\x00\x00" * 320, 16000, 1, context_id=context_id)


class Capture(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.frames = []
        self.started = asyncio.Event()
        self.ended = asyncio.Event()

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        self.frames.append(frame)
        if isinstance(frame, StartFrame):
            self.started.set()
        if isinstance(frame, LLMFullResponseEndFrame):
            self.ended.set()
        await self.push_frame(frame, direction)


CASES = [
    ('Goodbye.q{"call_id":"default_api:', "Goodbye."),
    ('Done.task_update_inventory_72:{"', "Done."),
    ("Done. item_code: null, batch_key: null.", "Done."),
    ("Hello {{Example Studio}}.", "Hello Example Studio."),
    (
        "mode:no_pending_tasks action:request_item_code|confirm_delivery|close_session",
        "",
    ),
    ('Hello. {"debug":"Private. Internal."} Welcome.', "Hello. Welcome."),
    ("Hello. {{unused_placeholder}}", "Hello."),
    ("Hello. default_api:task_update_inventory{", "Hello."),
    ("Ready. [true, false, null, -1]", "Ready."),
    (
        (
            "Ready. <call:default_api:task_update_inventory>Private planning. "
            "default_api:close_session{}</call:default_api:task_update_inventory> Welcome."
        ),
        "Ready. Welcome.",
    ),
]

TOOL_SCHEMAS = {
    name: {"type": "object", "properties": {}, "required": []}
    for name in (
        "task_update_inventory",
        "task_update_inventory_72",
        "request_item_code",
        "confirm_delivery",
        "close_session",
    )
}


def normalized(text):
    return " ".join(text.split())


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "expected"), CASES)
@pytest.mark.parametrize("mode", ["sentence", "words", "missing_words"])
@pytest.mark.parametrize("token_streaming", [False, True])
@pytest.mark.parametrize("upstream_guard", [False, True])
@pytest.mark.parametrize("pre_aggregated", [False, True])
async def test_speech_context_and_persisted_transcript_agree(
    text, expected, mode, token_streaming, upstream_guard, pre_aggregated
):
    context = LLMContext()
    assistant = LLMAssistantAggregator(context)
    tts = OfflineTTS(mode, token_streaming)
    sink = Capture()
    logs = InMemoryLogsBuffer(workflow_run_id=1)
    coordinator = TranscriptLogCoordinator(logs)
    persisted = asyncio.Event()
    record = coordinator.record_assistant_transcript

    async def record_and_signal(**kwargs):
        await record(**kwargs)
        persisted.set()

    coordinator.record_assistant_transcript = record_and_signal
    unused_user = SimpleNamespace(event_handler=lambda *_: lambda handler: handler)
    register_turn_log_handlers(coordinator, unused_user, assistant)
    await coordinator.record_turn_started(1)
    pipeline = Pipeline([tts, sink, assistant])
    tasks = TaskManager()
    await pipeline.setup(
        FrameProcessorSetup(
            clock=SystemClock(),
            task_manager=tasks,
            pipeline_worker=SimpleNamespace(app_resources={}, worker_runner=None),
        )
    )
    try:
        await pipeline.queue_frame(StartFrame())
        await asyncio.wait_for(sink.started.wait(), 2)
        await pipeline.queue_frame(LLMFullResponseStartFrame())
        guard = SpokenToolCallGuard(TOOL_SCHEMAS) if upstream_guard else None
        for char in text:
            chunk = guard.feed(char) if guard else char
            await pipeline.queue_frame(
                AggregatedTextFrame(chunk, AggregationType.TOKEN, raw_text=chunk)
                if pre_aggregated
                else LLMTextFrame(chunk)
            )
        if guard:
            tail = guard.finish()
            await pipeline.queue_frame(
                AggregatedTextFrame(tail, AggregationType.TOKEN, raw_text=tail)
                if pre_aggregated
                else LLMTextFrame(tail)
            )
            assert not guard.calls
        await pipeline.queue_frame(LLMFullResponseEndFrame())
        await asyncio.wait_for(sink.ended.wait(), 2)
        if expected:
            await asyncio.wait_for(persisted.wait(), 2)
        await coordinator.flush()
        separator = "" if token_streaming else " "
        assert normalized(separator.join(tts.requests)) == expected
        history = " ".join(
            m["content"] for m in context.messages if m.get("role") == "assistant"
        )
        assert normalized(history) == expected
        transcript = " ".join(e["payload"]["text"] for e in logs.get_events())
        assert normalized(transcript) == expected
        for frame in sink.frames:
            if isinstance(frame, (AggregatedTextFrame, TTSTextFrame)):
                assert "{" not in frame.text
                assert "{" not in (frame.raw_text or "")
                assert "default_api" not in (frame.raw_text or frame.text)
    finally:
        await pipeline.cleanup()
    assert not tasks.current_tasks()


def test_advertised_tool_fixture_can_recover_a_complete_valid_call():
    guard = SpokenToolCallGuard(TOOL_SCHEMAS)
    speech = guard.feed("default_api:task_update_inventory{}") + guard.finish()
    assert not speech.strip()
    assert [(call.name, call.arguments) for call in guard.calls] == [
        ("task_update_inventory", {})
    ]


@pytest.mark.asyncio
async def test_queued_speech_log_uses_the_same_sanitization():
    logs = InMemoryLogsBuffer(workflow_run_id=1)
    observer = RealtimeFeedbackObserver(AsyncMock(), logs)
    await observer.log_speech("Welcome {{Example Studio}}. item_code: null.")
    await observer.log_speech('{"debug":"private"}')
    assert [e["payload"]["text"].strip() for e in logs.get_events()] == [
        "Welcome Example Studio."
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sentence", "words"])
@pytest.mark.parametrize("token_streaming", [False, True])
async def test_queued_speech_and_interruption_do_not_share_pending_control_text(
    mode, token_streaming
):
    tts = OfflineTTS(mode, token_streaming)
    context = LLMContext()
    assistant = LLMAssistantAggregator(context)
    await run_test(
        Pipeline([tts, assistant]),
        frames_to_send=[
            LLMFullResponseStartFrame(),
            LLMTextFrame('{"debug":"unfinished'),
            SleepFrame(sleep=0.01),
            TTSSpeakFrame("Hello {{Example Studio}}.", append_to_context=True),
            SleepFrame(sleep=0.05),
            InterruptionFrame(),
            SleepFrame(sleep=0.01),
            LLMFullResponseStartFrame(),
            LLMTextFrame("Welcome."),
            LLMFullResponseEndFrame(),
            SleepFrame(sleep=0.05),
        ],
        expected_down_frames=None,
    )
    assert normalized(" ".join(tts.requests)) == "Hello Example Studio. Welcome."
    history = " ".join(
        m["content"] for m in context.messages if m.get("role") == "assistant"
    )
    assert normalized(history) == "Hello Example Studio. Welcome."


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["sentence", "words", "missing_words"])
async def test_post_aggregation_filters_clean_context_but_pronunciation_does_not(mode):
    class RemoveAnnotation(BaseTextFilter):
        async def filter(self, text):
            return text.replace("debug_annotation", "")

    async def pronounce(text, aggregation_type):
        return text.replace("Hello", "HELLO")

    tts = OfflineTTS(mode, token_streaming=False)
    tts._text_filters = [RemoveAnnotation()]
    tts._text_transforms = [("*", pronounce)]
    context = LLMContext()
    await run_test(
        Pipeline([tts, LLMAssistantAggregator(context)]),
        frames_to_send=[
            LLMFullResponseStartFrame(),
            LLMTextFrame("Hello debug_annotation world."),
            LLMFullResponseEndFrame(),
            SleepFrame(sleep=0.05),
        ],
    )
    assert normalized(" ".join(tts.requests)) == "HELLO world."
    history = " ".join(
        m["content"] for m in context.messages if m.get("role") == "assistant"
    )
    assert normalized(history) == "Hello world."
