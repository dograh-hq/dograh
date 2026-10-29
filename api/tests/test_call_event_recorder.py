"""Unit tests for the diagnostics observer and the refusal-filter drop hook.

The observer is driven directly through ``on_push_frame`` with synthetic
``FramePushed`` events plus the handful of public methods the wiring calls from
pipecat event handlers, so no pipeline is needed.
"""

import asyncio
from unittest.mock import create_autospec

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InputAudioRawFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSStartedFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.observers.user_bot_latency_observer import (
    LatencyBreakdown,
    TTFBBreakdownMetrics,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.base_output import BaseOutputTransport

from api.services.observability.call_events import events as ev
from api.services.observability.call_events.recorder import CallEventRecorder


class _RecordingSink:
    """Duck-typed stand-in for DiagnosticsSink."""

    def __init__(self):
        self.events = []

    @property
    def enabled(self) -> bool:
        return True

    def emit(self, event) -> None:
        self.events.append(event)

    def names(self) -> list[str]:
        return [e.event for e in self.events]

    def first(self, name: str):
        return next(e for e in self.events if e.event == name)

    def count(self, name: str) -> int:
        return sum(1 for e in self.events if e.event == name)


class _FakeEngine:
    """Engine attributes the observer snapshots, nothing else."""

    def __init__(self):
        self._queued_speech_mute_state = "waiting"
        self._mute_pipeline = False
        self._gathered_context = {"call_disposition": "completed"}


def _observer(**kwargs) -> CallEventRecorder:
    kwargs.setdefault("hung_after_s", 0.05)
    kwargs.setdefault("silent_after_s", 0.05)
    return CallEventRecorder(
        sink=kwargs.pop("sink"),
        run_id=42,
        org_id=7,
        workflow_id=3,
        engine=kwargs.pop("engine", _FakeEngine()),
        **kwargs,
    )


def _pushed(frame, source=None) -> FramePushed:
    return FramePushed(
        source=source or object(),
        destination=object(),
        frame=frame,
        direction=FrameDirection.DOWNSTREAM,
        timestamp=0,
    )


def _transport():
    """A source that passes the ``isinstance(source, BaseOutputTransport)`` gate."""
    return create_autospec(BaseOutputTransport, instance=True)


def _transcription(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(
        text=text, user_id="user", timestamp="2026-09-04T10:00:00Z"
    )


async def test_audio_frames_are_ignored():
    sink = _RecordingSink()
    observer = _observer(sink=sink)

    await observer.on_push_frame(
        _pushed(
            InputAudioRawFrame(audio=b"\x00\x00", sample_rate=16000, num_channels=1)
        )
    )

    assert sink.events == []


async def test_bot_speaking_counted_once_and_only_from_the_transport():
    sink = _RecordingSink()
    observer = _observer(sink=sink)
    frame = BotStartedSpeakingFrame()

    # Pushed between two ordinary processors first: ignored, not marked seen.
    await observer.on_push_frame(_pushed(frame))
    assert sink.names() == []

    await observer.on_push_frame(_pushed(frame, source=_transport()))
    await observer.on_push_frame(_pushed(frame, source=_transport()))

    assert sink.names() == [ev.BOT_SPEAKING_START]


async def test_mute_start_and_stop_report_the_duration_and_engine_state():
    sink = _RecordingSink()
    engine = _FakeEngine()
    observer = _observer(sink=sink, engine=engine)

    observer.on_mute_started()
    await asyncio.sleep(0.03)
    observer.on_mute_stopped()

    started = sink.first(ev.MUTE_STARTED)
    stopped = sink.first(ev.MUTE_STOPPED)
    assert started.detail == {
        "first_bot_complete_pending": True,
        "queued_speech_state": "waiting",
        "pipeline_muted": False,
        "function_calls_in_progress_n": 0,
    }
    assert set(stopped.detail) == set(started.detail)
    assert stopped.value_ms >= 25
    assert observer.mute_ms_total >= 25


async def test_llm_response_without_text_is_reported_empty():
    sink = _RecordingSink()
    observer = _observer(sink=sink)

    await observer.on_push_frame(_pushed(LLMFullResponseStartFrame()))
    await observer.on_push_frame(_pushed(LLMFullResponseEndFrame()))

    empty = sink.first(ev.LLM_RESPONSE_EMPTY)
    assert empty.severity == ev.SEVERITY_WARN

    sink.events.clear()
    await observer.on_push_frame(_pushed(LLMFullResponseStartFrame()))
    await observer.on_push_frame(_pushed(LLMTextFrame("Buongiorno")))
    await observer.on_push_frame(_pushed(LLMFullResponseEndFrame()))

    assert ev.LLM_RESPONSE_EMPTY not in sink.names()


async def test_function_call_hung_then_still_reports_the_true_duration():
    sink = _RecordingSink()
    observer = _observer(sink=sink)

    await observer.on_push_frame(
        _pushed(
            FunctionCallInProgressFrame(
                function_name="transition_to_node",
                tool_call_id="call-1",
                arguments={},
            )
        )
    )
    await asyncio.sleep(0.12)

    hung = sink.first(ev.FUNCTION_CALL_HUNG)
    assert hung.severity == ev.SEVERITY_WARN
    assert hung.detail["name"] == "transition_to_node"
    assert hung.value_ms >= 50

    await observer.on_push_frame(
        _pushed(
            FunctionCallResultFrame(
                function_name="transition_to_node",
                tool_call_id="call-1",
                arguments={},
                result="ok",
            )
        )
    )

    ended = sink.first(ev.FUNCTION_CALL_ENDED)
    assert ended.value_ms >= hung.value_ms
    assert observer.hung_function_calls == 1


async def test_bot_silent_after_user_turn_snapshot():
    sink = _RecordingSink()
    observer = _observer(sink=sink)

    await observer.on_push_frame(
        _pushed(
            FunctionCallInProgressFrame(
                function_name="check_availability", tool_call_id="call-2", arguments={}
            )
        )
    )
    observer.on_user_turn_started(None)
    await observer.on_push_frame(_pushed(LLMFullResponseStartFrame()))
    observer.on_refusal_dropped("I'm sorry, I cannot respond to that query.")
    observer.on_user_turn_stopped(None)
    await asyncio.sleep(0.09)

    silent = sink.first(ev.BOT_SILENT_AFTER_USER_TURN)
    assert silent.severity == ev.SEVERITY_WARN
    assert silent.turn == 1
    assert set(silent.detail) == {
        "elapsed_ms",
        "muted",
        "function_calls_in_progress",
        "function_calls_in_progress_n",
        "llm_response_open",
        "llm_chars_since_turn",
        "refusals_dropped_since_turn",
        "tts_started_since_turn",
    }
    assert silent.detail["function_calls_in_progress"] == ["check_availability"]
    assert silent.detail["function_calls_in_progress_n"] == 1
    assert silent.detail["llm_response_open"] is True
    assert silent.detail["llm_chars_since_turn"] == 0
    assert silent.detail["refusals_dropped_since_turn"] == 1
    assert silent.detail["tts_started_since_turn"] is False
    assert silent.detail["muted"] is False


async def test_bot_speech_cancels_the_silence_watchdog():
    sink = _RecordingSink()
    observer = _observer(sink=sink)

    observer.on_user_turn_started(None)
    observer.on_user_turn_stopped(None)
    await observer.on_push_frame(
        _pushed(BotStartedSpeakingFrame(), source=_transport())
    )
    await asyncio.sleep(0.09)

    assert ev.BOT_SILENT_AFTER_USER_TURN not in sink.names()


async def test_call_ended_disarms_the_silence_watchdog():
    """The caller hung up mid-turn: that is not a bot that went silent."""
    sink = _RecordingSink()
    observer = _observer(sink=sink)

    observer.on_user_turn_started(None)
    observer.on_user_turn_stopped(None)
    observer.call_ended("customer_hangup", 12.0)
    await asyncio.sleep(0.09)

    assert ev.BOT_SILENT_AFTER_USER_TURN not in sink.names()
    assert sink.names()[-1] == ev.CALL_ENDED


async def test_call_ended_disarms_the_hung_function_call_watchdog():
    """No row may land after the summary, not even from a pending watchdog."""
    sink = _RecordingSink()
    observer = _observer(sink=sink)

    await observer.on_push_frame(
        _pushed(
            FunctionCallInProgressFrame(
                function_name="transition_to_node",
                tool_call_id="call-3",
                arguments={},
            )
        )
    )
    observer.call_ended("customer_hangup", 12.0)
    await asyncio.sleep(0.12)

    assert ev.FUNCTION_CALL_HUNG not in sink.names()
    assert observer.hung_function_calls == 0
    assert sink.names()[-1] == ev.CALL_ENDED


async def test_latency_breakdown_uses_the_contract_field_names():
    sink = _RecordingSink()
    observer = _observer(sink=sink)

    # e2e comes from pipecat's own measurement, handed over just before the
    # breakdown for the same cycle.
    observer.on_latency_measured(1.75)
    observer.on_latency_breakdown(
        LatencyBreakdown(
            ttfb=[
                TTFBBreakdownMetrics(
                    processor="ExampleSTTService#3", start_time=0.0, duration_secs=0.12
                ),
                TTFBBreakdownMetrics(
                    processor="ExampleLLMService#19",
                    start_time=0.0,
                    duration_secs=0.44,
                ),
                TTFBBreakdownMetrics(
                    processor="ExampleTTSService#11",
                    start_time=0.0,
                    duration_secs=0.31,
                ),
            ],
            user_turn_secs=0.8,
        )
    )

    breakdown = sink.first(ev.LATENCY_BREAKDOWN)
    assert breakdown.detail["user_turn_ms"] == 800.0
    assert breakdown.detail["stt_ttfb_ms"] == 120.0
    assert breakdown.detail["llm_ttfb_ms"] == 440.0
    assert breakdown.detail["tts_ttfb_ms"] == 310.0
    assert breakdown.detail["e2e_ms"] == breakdown.value_ms == 1750.0


async def test_turn_to_audio_ms_measures_turn_release_to_first_tts_audio():
    """The published metric starts at the turn release, not at user silence."""
    sink = _RecordingSink()
    observer = _observer(sink=sink)

    # Silence at t=100.0, turn released 0.6s later, first TTS audio at t=102.1.
    observer.on_latency_measured(2.5)
    observer.on_latency_breakdown(
        LatencyBreakdown(
            ttfb=[
                TTFBBreakdownMetrics(
                    processor="ExampleLLMService#19",
                    start_time=100.6,
                    duration_secs=0.7,
                ),
                TTFBBreakdownMetrics(
                    processor="ExampleTTSService#11",
                    start_time=101.3,
                    duration_secs=0.8,
                ),
            ],
            user_turn_start_time=100.0,
            user_turn_secs=0.6,
        )
    )

    detail = sink.first(ev.LATENCY_BREAKDOWN).detail
    assert detail["turn_to_audio_ms"] == 1500.0  # 102.1 - 100.6
    # The raw pipecat number stays written, and stays bigger: it carries the VAD
    # window, the turn wait and the playback buffer.
    assert detail["e2e_ms"] == 2500.0


async def test_turn_to_audio_ms_keeps_the_first_tts_audio_not_the_worst():
    sink = _RecordingSink()
    observer = _observer(sink=sink)

    observer.on_latency_breakdown(
        LatencyBreakdown(
            ttfb=[
                TTFBBreakdownMetrics(
                    processor="ExampleTTSService#11",
                    start_time=100.5,
                    duration_secs=2.0,
                ),
                TTFBBreakdownMetrics(
                    processor="ExampleTTSService#11",
                    start_time=100.5,
                    duration_secs=0.4,
                ),
            ],
            user_turn_start_time=100.0,
            user_turn_secs=0.5,
        )
    )

    detail = sink.first(ev.LATENCY_BREAKDOWN).detail
    assert detail["turn_to_audio_ms"] == 400.0
    # The per-stage column answers a different question and keeps the worst one.
    assert detail["tts_ttfb_ms"] == 2000.0


async def test_turn_to_audio_ms_is_none_without_both_ends():
    """No turn release, no TTS audio, or an audio older than the release: None.

    Never zero, and never fallen back to `e2e_ms`: two definitions in one column
    publish an average of which measurement happened to be available.
    """
    sink = _RecordingSink()
    observer = _observer(sink=sink)

    tts = TTFBBreakdownMetrics(
        processor="ExampleTTSService#11",
        start_time=101.0,
        duration_secs=0.4,
    )
    observer.on_latency_measured(2.5)
    # 1) no turn release (the greeting window)
    observer.on_latency_breakdown(LatencyBreakdown(ttfb=[tts]))
    # 2) turn released, but no TTS metric (an unpatched pipeline)
    observer.on_latency_breakdown(
        LatencyBreakdown(user_turn_start_time=100.0, user_turn_secs=0.5)
    )
    # 3) audio that precedes the release: a leftover from the previous turn
    observer.on_latency_breakdown(
        LatencyBreakdown(ttfb=[tts], user_turn_start_time=200.0, user_turn_secs=0.5)
    )

    for e in (x for x in sink.events if x.event == ev.LATENCY_BREAKDOWN):
        assert e.detail["turn_to_audio_ms"] is None


async def test_latency_breakdown_without_a_measurement_reports_no_e2e():
    """The first-bot-speech breakdown has no user->bot cycle behind it."""
    sink = _RecordingSink()
    observer = _observer(sink=sink)

    # A previous cycle's value must not leak into the next breakdown either.
    observer.on_latency_measured(1.75)
    observer.on_latency_breakdown(LatencyBreakdown(user_turn_secs=0.8))
    observer.on_latency_breakdown(LatencyBreakdown(user_turn_secs=0.8))

    first, second = (e for e in sink.events if e.event == ev.LATENCY_BREAKDOWN)
    assert first.value_ms == 1750.0
    assert second.value_ms is None
    assert second.detail["e2e_ms"] is None


async def test_bot_speaking_start_still_carries_the_frame_measured_latency():
    """bot_speaking_start is emitted without the latency observer attached."""
    sink = _RecordingSink()
    observer = _observer(sink=sink)

    await observer.on_push_frame(_pushed(VADUserStoppedSpeakingFrame(stop_secs=0.2)))
    await asyncio.sleep(0.02)
    await observer.on_push_frame(
        _pushed(BotStartedSpeakingFrame(), source=_transport())
    )

    started = sink.first(ev.BOT_SPEAKING_START)
    assert started.value_ms >= 200  # stop_secs is added back by the VAD frame


async def test_call_ended_summary_counts():
    sink = _RecordingSink()
    observer = _observer(sink=sink)

    await observer.on_push_frame(_pushed(VADUserStartedSpeakingFrame()))
    await observer.on_push_frame(_pushed(_transcription("")))
    await observer.on_push_frame(_pushed(_transcription("va bene")))
    await observer.on_push_frame(_pushed(InterruptionFrame()))
    await observer.on_push_frame(_pushed(TTSStartedFrame()))
    observer.on_user_turn_started(None)
    observer.on_user_turn_stop_timeout()
    observer.on_user_turn_idle(retry=1, total=2)
    observer.on_refusal_dropped("I'm sorry, I cannot help with that.")
    await observer.on_push_frame(_pushed(LLMFullResponseStartFrame()))
    await observer.on_push_frame(_pushed(LLMFullResponseEndFrame()))
    observer.on_mute_started()
    observer.on_mute_stopped()

    observer.call_ended("completed", 61.5, end_reason="call_transferred")

    summary = sink.first(ev.CALL_ENDED)
    assert set(summary.detail) == {
        "reason",
        "end_reason",
        "duration_s",
        "turns",
        "user_speech",
        "mute_ms_total",
        "idle_events",
        "empty_finals",
        "hung_function_calls",
        "silent_after_turn",
        "refusals_dropped",
        "llm_empty_responses",
        "stop_timeouts",
        "interruptions",
        "e2e_p50_ms",
        "e2e_max_ms",
        "user_turn_p50_ms",
        "user_turn_max_ms",
        "host",
    }
    assert summary.detail["reason"] == "completed"
    # The raw EndTaskReason, not the business disposition: the two differ and
    # KPIs on how calls terminate read this one.
    assert summary.detail["end_reason"] == "call_transferred"
    assert summary.detail["duration_s"] == 61.5
    assert summary.detail["turns"] == 1
    assert summary.detail["user_speech"] is True
    assert summary.detail["empty_finals"] == 1
    assert summary.detail["stop_timeouts"] == 1
    assert summary.detail["idle_events"] == 1
    assert summary.detail["interruptions"] == 1
    assert summary.detail["refusals_dropped"] == 1
    assert summary.detail["llm_empty_responses"] == 1
    assert summary.detail["hung_function_calls"] == 0
    assert summary.detail["silent_after_turn"] == 0

    # A second call (pipeline_finished racing a disconnect) must not duplicate.
    observer.call_ended("completed", 61.5, end_reason="call_transferred")
    assert sink.count(ev.CALL_ENDED) == 1

    await observer.cleanup()


async def test_call_ended_carries_the_host(monkeypatch):
    """Multi-host installations can tell which instance handled each call."""
    sink = _RecordingSink()
    observer = _observer(sink=sink)
    monkeypatch.setenv("DOGRAH_INSTANCE", "api-2")

    observer.call_ended("completed", 1.0)

    assert sink.first(ev.CALL_ENDED).detail["host"] == "api-2"


async def test_call_ended_host_falls_back_to_the_hostname(monkeypatch):
    """Without DOGRAH_INSTANCE, the hostname identifies the host."""
    import socket

    sink = _RecordingSink()
    observer = _observer(sink=sink)
    monkeypatch.delenv("DOGRAH_INSTANCE", raising=False)

    observer.call_ended("completed", 1.0)

    assert sink.first(ev.CALL_ENDED).detail["host"] == socket.gethostname()


async def test_call_ended_without_an_end_reason_reports_unknown():
    """Pipeline finished without end_call_with_reason: no raw reason exists."""
    sink = _RecordingSink()
    observer = _observer(sink=sink)

    observer.call_ended("customer_hangup", 3.0)

    summary = sink.first(ev.CALL_ENDED)
    assert summary.detail["end_reason"] == "unknown"
    assert summary.detail["reason"] == "customer_hangup"

    await observer.cleanup()


async def test_a_broken_handler_never_escapes_into_the_pipeline():
    class _ExplodingSink(_RecordingSink):
        def emit(self, event):
            raise RuntimeError("diagnostics is broken")

    observer = _observer(sink=_ExplodingSink())

    await observer.on_push_frame(
        _pushed(BotStoppedSpeakingFrame(), source=_transport())
    )
    observer.on_user_turn_started(None)
    observer.call_ended("completed", 1.0)
