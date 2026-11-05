import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from pipecat.frames.frames import ErrorFrame, InterruptionFrame, TranscriptionFrame
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection
from pipecat.utils.base_object import BaseObject

from api.services.observability.call_events import runtime
from api.services.observability.call_events.base import EventBuffer
from api.services.observability.call_events.configuration import CallEventsSettings
from api.services.observability.call_events.recorder import CallEventRecorder
from api.services.pipecat.in_memory_buffers import InMemoryLogsBuffer
from api.services.pipecat.realtime_feedback_observer import RealtimeFeedbackObserver


class Publisher(BaseObject):
    def __init__(self, names):
        super().__init__()
        for name in names:
            self._register_event_handler(name)


async def test_session_drains_callbacks_finishes_once_and_does_not_persist(monkeypatch):
    user = Publisher(
        [
            "on_user_turn_started",
            "on_user_turn_stopped",
            "on_user_turn_stop_timeout",
            "on_user_mute_started",
            "on_user_mute_stopped",
        ]
    )
    latency = Publisher(
        ["on_latency_measured", "on_latency_breakdown", "on_first_bot_speech_latency"]
    )
    task = Publisher(["on_idle_timeout", "on_heartbeat_timeout", "on_pipeline_error"])
    task.user_bot_latency_observer = latency
    task.wait_for_observers = AsyncMock()
    engine = SimpleNamespace(
        _gathered_context={"call_disposition": "sale", "call_status": "user_hangup"}
    )
    session = runtime.CallEventsSession(
        settings=CallEventsSettings(enabled=True, sink_type="fake"),
        organization_id=7,
        run_id=42,
        workflow_id=3,
        engine=engine,
    )
    monitor = SimpleNamespace()
    session.attach(task, user, monitor)
    submit = Mock()
    monkeypatch.setattr(runtime.delivery, "submit", submit)
    await user._call_event_handler("on_user_turn_started", SimpleNamespace())
    await latency._call_event_handler("on_first_bot_speech_latency", 0.2)
    monitor.on_idle_diagnostic(retry=2, total=3)
    await session.finish()
    await session.finish()
    submit.assert_called_once()
    org, _, events, _ = submit.call_args.args
    assert org == 7
    assert [e.event for e in events][-1] == "call_ended"
    assert {e.event for e in events} == {
        "user_turn_idle",
        "user_turn_started",
        "first_bot_speech_latency",
        "call_ended",
    }
    assert events[-1].detail["end_reason"] == "user_hangup"
    assert events[-1].detail["reason"] == "sale"
    assert events[-1].detail["turns"] == 1
    assert monitor.on_idle_diagnostic is None
    assert not session.buffer.events
    assert not session.recorder._watchdog_tasks
    assert not user._event_handlers["on_user_turn_started"].handlers


async def test_finish_waits_for_worker_callbacks_already_issued(monkeypatch):
    user = Publisher(["on_user_turn_started"])
    task = Publisher(["on_idle_timeout", "on_heartbeat_timeout", "on_pipeline_error"])
    task.user_bot_latency_observer = None

    async def wait_for_observers():
        # The worker issues a callback in the loop turn that resumes finish():
        # its handler task exists, but has not run when the batch is drained.
        loop = asyncio.get_running_loop()
        loop.call_soon(
            lambda: loop.create_task(task._call_event_handler("on_heartbeat_timeout"))
        )

    task.wait_for_observers = wait_for_observers
    session = runtime.CallEventsSession(
        settings=CallEventsSettings(enabled=True, sink_type="fake"),
        organization_id=7,
        run_id=42,
        workflow_id=3,
        engine=SimpleNamespace(_gathered_context={}),
    )
    session.attach(task, user, SimpleNamespace())
    submit = Mock()
    monkeypatch.setattr(runtime.delivery, "submit", submit)

    await session.finish()

    events = submit.call_args.args[2]
    assert [e.event for e in events] == ["heartbeat_timeout", "call_ended"]


async def test_existing_observer_feeds_separate_recorder_before_rtf_filters():
    buffer = EventBuffer()
    recorder = CallEventRecorder(sink=buffer, run_id=1, org_id=2, workflow_id=3)
    logs = InMemoryLogsBuffer(workflow_run_id=1)
    observer = RealtimeFeedbackObserver(
        ws_sender=AsyncMock(), logs_buffer=logs, call_event_recorder=recorder
    )
    error = ErrorFrame("failure")
    frames = [InterruptionFrame(), TranscriptionFrame("", "user", "timestamp"), error]
    for frame in frames:
        direction = (
            FrameDirection.DOWNSTREAM
            if isinstance(frame, InterruptionFrame)
            else FrameDirection.UPSTREAM
        )
        data = FramePushed(
            source=SimpleNamespace(),
            destination=SimpleNamespace(),
            frame=frame,
            direction=direction,
            timestamp=0,
        )
        await observer.on_push_frame(data)
        await observer.on_push_frame(data)
    recorder.on_pipeline_error(error)
    assert [e.event for e in buffer.events] == [
        "interruption",
        "transcript_final",
        "pipeline_error",
    ]
    # Only the existing RTF error event is persisted; no diagnostic rows leak in.
    assert all(e["type"].startswith("rtf-") for e in logs.get_events())
    assert all("event" not in e for e in logs.get_events())
    await recorder.cleanup()


async def test_disabled_and_invalid_config_do_not_affect_call(monkeypatch):
    monkeypatch.setattr(
        runtime, "load_settings", AsyncMock(return_value=CallEventsSettings())
    )
    assert (
        await runtime.create_session(
            organization_id=7, run_id=1, workflow_id=2, engine=None
        )
        is None
    )
    runtime.load_settings.side_effect = RuntimeError("database unavailable")
    assert (
        await runtime.create_session(
            organization_id=7, run_id=1, workflow_id=2, engine=None
        )
        is None
    )


def test_current_playback_preserves_legacy_mute_state_values():
    speech = SimpleNamespace(mute_user=True, done=False, started=False)
    engine = SimpleNamespace(
        speech_playback=SimpleNamespace(pending={"speech": speech})
    )
    recorder = CallEventRecorder(
        sink=EventBuffer(), run_id=1, org_id=2, workflow_id=3, engine=engine
    )
    assert recorder._mute_snapshot()["queued_speech_state"] == "waiting"
    speech.started = True
    assert recorder._mute_snapshot()["queued_speech_state"] == "playing"
    speech.done = True
    assert recorder._mute_snapshot()["queued_speech_state"] == "idle"


async def test_retired_agent_tool_completion_is_kept_but_late_text_is_not():
    from unittest.mock import create_autospec

    from pipecat.frames.frames import (
        FunctionCallInProgressFrame,
        FunctionCallResultFrame,
        LLMTextFrame,
    )

    from api.services.pipecat.agent_bridge import AgentWorker

    worker = create_autospec(AgentWorker, instance=True)
    worker.name = "old"
    old = SimpleNamespace(
        visit_id="old", current_node=SimpleNamespace(id="old-node", name="Old")
    )
    new = SimpleNamespace(
        visit_id="new", current_node=SimpleNamespace(id="new-node", name="New")
    )
    engine = SimpleNamespace(active_agent=old, _retired_agents=[])
    buffer = EventBuffer()
    recorder = CallEventRecorder(
        sink=buffer, run_id=1, org_id=2, workflow_id=3, engine=engine
    )

    async def push(frame):
        await recorder.on_push_frame(
            FramePushed(
                source=SimpleNamespace(pipeline_worker=worker),
                destination=SimpleNamespace(),
                frame=frame,
                direction=FrameDirection.DOWNSTREAM,
                timestamp=0,
            )
        )

    await push(
        FunctionCallInProgressFrame(
            function_name="transfer", tool_call_id="t1", arguments={}
        )
    )
    engine.active_agent = new
    engine._retired_agents.append(old)
    await push(LLMTextFrame("late text"))
    await push(
        FunctionCallResultFrame(
            function_name="transfer", tool_call_id="t1", arguments={}, result={}
        )
    )
    await push(ErrorFrame("retired failure"))
    assert [e.event for e in buffer.events] == [
        "function_call_started",
        "function_call_ended",
        "pipeline_error",
    ]
    assert all(e.node_id == "old-node" for e in buffer.events)
    assert recorder._llm_text_chars == 0
    assert not recorder._function_calls
    await recorder.cleanup()
