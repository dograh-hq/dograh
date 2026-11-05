"""Tests for the agent lines DeferredTunerObserver keeps in its Tuner snapshot.

Frames are pushed in the order a live call produces them, on a fake clock, and the
snapshot Dograh stores for delivery is checked.

Covers:
- Static TTS (``append_to_context=False``), such as a greeting or a disclosure. It is
  spoken but never committed to the LLM context.
- A read-back whose context commit lands after the caller answers and the LLM calls a
  tool
- That the SDK never forwards spans to Tuner with the placeholder credentials
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    LLMContextAssistantTimestampFrame,
    LLMContextAssistantTurnFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    StartFrame,
    TranscriptionFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

from api.services.integrations.tuner.collector import DeferredTunerObserver

BASE_NS = 1_700_000_000_000_000_000

GREETING = "Hello, this call is recorded for quality. Is that okay with you?"
FIRST_QUESTION = "Can you confirm your date of birth?"
READBACK = "So that's one tablet twice a day. Is that right?"
NEXT_QUESTION = "Added. What's the next one?"


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(BASE_NS / 1e9 + ms / 1000, tz=UTC).isoformat()


class _Call:
    """Frames for one call, in milliseconds from its start."""

    def __init__(self, monkeypatch) -> None:
        self._now = BASE_NS
        monkeypatch.setattr("time.time_ns", lambda: self._now)
        self.observer = DeferredTunerObserver(workflow_run_id=1, call_type="phone_call")
        self._frames: list[tuple[int, object]] = [(0, StartFrame())]
        self._tool_calls = 0

    def static(self, text: str, start: int, end: int) -> None:
        frame = TTSTextFrame(text, text_type="sentence")
        frame.append_to_context = False
        self._frames += [
            (start - 50, frame),
            (start, BotStartedSpeakingFrame()),
            (end, BotStoppedSpeakingFrame()),
        ]

    def commit(self, text: str, ms: int, started: int) -> None:
        # Pipecat 1.4+ sends both when it writes an assistant turn to the context.
        self._frames += [
            (ms, LLMContextAssistantTimestampFrame(timestamp=_iso(ms))),
            (ms + 1, LLMContextAssistantTurnFrame(text=text, timestamp=_iso(started))),
        ]

    def reply(self, text: str, start: int, end: int, *, commit: bool = True) -> None:
        self._frames += [
            (start - 400, LLMFullResponseStartFrame()),
            (start - 300, LLMTextFrame(text)),
            (start - 200, LLMFullResponseEndFrame()),
            (start - 50, TTSTextFrame(text, text_type="sentence")),
            (start, BotStartedSpeakingFrame()),
            (end, BotStoppedSpeakingFrame()),
        ]
        if commit:
            self.commit(text, end - 300, started=start - 100)

    def tool(self, name: str, start: int) -> None:
        self._tool_calls += 1
        call_id = f"call_{self._tool_calls}"
        self._frames += [
            (start, LLMFullResponseStartFrame()),
            (start + 10, FunctionCallInProgressFrame(name, call_id, {})),
            (start + 20, LLMFullResponseEndFrame()),
            (
                start + 100,
                FunctionCallResultFrame(name, call_id, {}, {"success": True}),
            ),
        ]

    def user(self, text: str, start: int, end: int) -> None:
        self._frames += [
            (start, UserStartedSpeakingFrame()),
            (start + 100, TranscriptionFrame(text, "caller", _iso(start + 100))),
            (end, UserStoppedSpeakingFrame()),
        ]

    async def agent_lines(self) -> list[str]:
        frames = sorted(self._frames, key=lambda item: item[0])
        frames.append((frames[-1][0] + 500, EndFrame()))
        for ms, frame in frames:
            self._now = BASE_NS + ms * 1_000_000
            await self.observer.on_push_frame(
                FramePushed(
                    source=SimpleNamespace(),
                    destination=SimpleNamespace(),
                    frame=frame,
                    direction=FrameDirection.DOWNSTREAM,
                    timestamp=0,
                )
            )
        payload = self.observer.build_payload_snapshot()
        return [
            row["text"]
            for row in payload["transcript_with_tool_calls"]
            if row["role"] == "agent"
        ]


async def test_static_greeting_is_kept_before_the_first_reply(monkeypatch):
    call = _Call(monkeypatch)
    call.static(GREETING, 1_000, 6_000)
    call.user("Yes.", 7_000, 7_600)
    call.reply(FIRST_QUESTION, 8_500, 10_500)

    assert await call.agent_lines() == [GREETING, FIRST_QUESTION]


async def test_read_back_committed_after_a_tool_call_is_kept_in_order(monkeypatch):
    call = _Call(monkeypatch)
    call.reply(READBACK, 1_000, 7_000, commit=False)
    call.user("Yes, that's right.", 8_000, 8_900)
    call.tool("add_medication", 9_400)
    call.commit(READBACK, 9_450, started=900)
    call.reply(NEXT_QUESTION, 10_200, 12_400)

    assert await call.agent_lines() == [READBACK, NEXT_QUESTION]


def test_spans_are_not_forwarded_with_placeholder_credentials(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "tuner_pipecat_sdk._base.setup_call_tracing",
        lambda **kwargs: calls.append(kwargs),
    )

    DeferredTunerObserver(workflow_run_id=1, call_type="phone_call")

    assert calls == []
