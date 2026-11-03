"""Replay a fixed sequence of facts against the golden contract fixture.

The fixture pins the wire contract, with clocks/hostname fixed. It is
deliberately not generated from the implementation under test.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import create_autospec

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TranscriptionFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.base_output import BaseOutputTransport

from api.services.integrations.bigquery.sink import to_bigquery_row
from api.services.observability.call_events import events
from api.services.observability.call_events import recorder as recorder_module


async def replay(recorder):
    """Exercise all 26 names without depending on pipeline scheduling."""
    output = create_autospec(BaseOutputTransport, instance=True)

    async def push(frame):
        await recorder.on_push_frame(
            FramePushed(
                source=output,
                destination=SimpleNamespace(),
                frame=frame,
                direction=FrameDirection.DOWNSTREAM,
                timestamp=0,
            )
        )

    def no_background(coroutine, name):
        coroutine.close()
        return None

    recorder._spawn = no_background
    recorder.on_user_turn_started(SimpleNamespace())
    await push(VADUserStartedSpeakingFrame())
    await push(VADUserStoppedSpeakingFrame(timestamp=100, stop_secs=0.2))
    await push(TranscriptionFrame("", "user", "timestamp"))
    recorder.on_mute_started()
    recorder.on_mute_stopped()
    await push(LLMFullResponseStartFrame())
    await push(LLMFullResponseEndFrame())
    await push(TTSStartedFrame())
    await push(TTSStoppedFrame())
    await push(
        FunctionCallInProgressFrame(
            function_name="lookup", tool_call_id="tool-1", arguments={}
        )
    )
    await recorder._hung_watchdog("tool-1")
    await push(
        FunctionCallResultFrame(
            function_name="lookup", tool_call_id="tool-1", arguments={}, result={}
        )
    )
    recorder.on_user_turn_stopped(SimpleNamespace(), "hello")
    await recorder._silence_watchdog(recorder.turn)
    await push(BotStartedSpeakingFrame())
    await push(BotStoppedSpeakingFrame())
    recorder.on_user_turn_stop_timeout()
    recorder.on_user_turn_idle(retry=1, total=3)
    recorder.on_latency_measured(0.5)
    recorder.on_latency_breakdown(
        SimpleNamespace(
            user_turn_start_time=100,
            user_turn_secs=0.2,
            ttfb=[
                SimpleNamespace(
                    processor="ExampleSTTService#1", start_time=100, duration_secs=0.1
                ),
                SimpleNamespace(
                    processor="ExampleLLMService#2", start_time=100.2, duration_secs=0.2
                ),
                SimpleNamespace(
                    processor="ExampleTTSService#3",
                    start_time=100.4,
                    duration_secs=0.1,
                ),
            ],
            text_aggregation=SimpleNamespace(duration_secs=0.01),
            function_calls=[SimpleNamespace(duration_secs=0.3)],
        )
    )
    recorder.on_first_bot_speech_latency(0.7)
    recorder.on_pipeline_error(ErrorFrame("provider unavailable", fatal=False))
    recorder.on_heartbeat_timeout()
    recorder.on_pipeline_idle_timeout()
    await push(InterruptionFrame())
    # Current pipeline has no refusal filter. Retain the event's wire contract
    # without wiring a synthetic refusal source into production.
    recorder.on_refusal_dropped("refused")
    recorder.call_ended("completed", duration_s=10, end_reason="user_hangup")
    await recorder.cleanup()


def fixture_engine():
    node = SimpleNamespace(id="node-1", name="Greeting")
    return SimpleNamespace(
        _current_node=node,
        active_agent=SimpleNamespace(current_node=node),
        _queued_speech_mute_state="waiting",
        _mute_pipeline=False,
    )


async def test_all_26_events_match_the_contract_fixture(monkeypatch):
    captured = []
    monkeypatch.setattr(
        recorder_module, "time", SimpleNamespace(time=lambda: 101, monotonic=lambda: 10)
    )
    monkeypatch.setattr(events, "host_name", lambda: "host-test")
    recorder = recorder_module.CallEventRecorder(
        sink=SimpleNamespace(emit=captured.append),
        run_id=42,
        org_id=7,
        workflow_id=3,
        engine=fixture_engine(),
        hung_after_s=0,
        silent_after_s=0,
    )
    await replay(recorder)
    rows = []
    for event in captured:
        event.ts = 1726326192.123
        rows.append(to_bigquery_row(event)["json"])
    expected = json.loads(
        (Path(__file__).parent / "fixtures/call_events_contract.json").read_text()
    )
    assert len({row["event"] for row in rows}) == 26
    assert rows == expected
