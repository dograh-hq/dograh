"""Engine contracts: one opening, bounded playback, and silent screening waits."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pipecat.frames.frames import CancelFrame, TTSSpeakFrame

from api.enums import AnswerAction
from api.schemas.answer_supervisor import AnswerMessage, AnswerSupervisorConfig
from api.services.pipecat.answer_classification import MachineSubtype
from api.services.pipecat.processors.answer_supervisor import AnswerVerdict
from api.services.workflow.answer_handling import handle_answer
from api.services.workflow.pipecat_engine import PipecatEngine


def make_call(verdicts, **settings):
    engine = PipecatEngine(workflow=None, call_context_vars={}, workflow_run_id=1)
    engine.workflow = SimpleNamespace(start_node_id="start")
    engine.task = SimpleNamespace(queue_frame=AsyncMock())
    engine.queue_node_opening = AsyncMock(return_value="greeting")
    engine.end_call_with_reason = AsyncMock()
    engine.wait_for_speech_playback = AsyncMock(return_value=True)
    supervisor = Mock()
    supervisor.config = AnswerSupervisorConfig(**settings)
    supervisor.wait_for_verdict = AsyncMock(side_effect=verdicts)
    supervisor.close = AsyncMock()
    supervisor.wait_closed = AsyncMock(side_effect=asyncio.Event().wait)
    return engine, supervisor, AsyncMock()


@pytest.mark.asyncio
@pytest.mark.parametrize("voicemail_action", ["hangup", "leave_message"])
async def test_voicemail_verdict_waits_for_playback_before_hangup(voicemail_action):
    engine, supervisor, idle = make_call(
        [
            AnswerVerdict(
                AnswerAction.LEAVE_MESSAGE, "voicemail", MachineSubtype.VOICEMAIL
            )
        ],
        voicemail_action=voicemail_action,
        voicemail_message=AnswerMessage(text="Please call us back."),
    )
    playback = asyncio.Event()
    engine.wait_for_speech_playback = AsyncMock(side_effect=playback.wait)
    running = asyncio.create_task(
        handle_answer(engine, supervisor, update_idle_timeout=idle)
    )
    async with asyncio.timeout(1):
        while not engine.wait_for_speech_playback.called:
            await asyncio.sleep(0)
    engine.end_call_with_reason.assert_not_awaited()
    frame = engine.task.queue_frame.call_args.args[0]
    assert isinstance(frame, TTSSpeakFrame)
    assert frame.text == "Please call us back."
    playback.set()
    await asyncio.wait_for(running, 1)
    assert engine.end_call_with_reason.call_args.args[0] == "voicemail_detected"
    engine.queue_node_opening.assert_not_awaited()


@pytest.mark.asyncio
async def test_screening_then_human_restores_idle_and_opens_once():
    engine, supervisor, idle = make_call(
        [
            AnswerVerdict(
                AnswerAction.SCREEN_THEN_REARM, "screener", MachineSubtype.SCREENER
            ),
            AnswerVerdict(
                AnswerAction.RELEASE, "human_turn", MachineSubtype.CONVERSATION
            ),
        ],
        screening_message=AnswerMessage(text="Alex calling about your appointment."),
    )
    await asyncio.wait_for(
        handle_answer(engine, supervisor, update_idle_timeout=idle), 1
    )
    supervisor.begin_screening_wait.assert_called_once()
    supervisor.release.assert_called_once()
    engine.queue_node_opening.assert_awaited_once()
    assert [c.args[0] for c in idle.await_args_list] == [0, None]
    assert engine._mute_pipeline is False
    assert engine._queued_speech_mute_state == "idle"
    engine.end_call_with_reason.assert_not_awaited()
    assert engine._gathered_context["answer_supervisor"] == [
        {
            "action": "screen_then_rearm",
            "reason": "screener",
            "subtype": "SCREENER",
            "screening_rearms": 0,
        },
        {
            "action": "release",
            "reason": "human_turn",
            "subtype": "CONVERSATION",
            "screening_rearms": 1,
        },
    ]


@pytest.mark.asyncio
async def test_screening_timeout_drops_without_opening_or_idle_reason():
    engine, supervisor, idle = make_call(
        [
            AnswerVerdict(
                AnswerAction.SCREEN_THEN_REARM, "screener", MachineSubtype.SCREENER
            ),
            AnswerVerdict(AnswerAction.DROP, "screening_timeout"),
        ],
        screening_message=AnswerMessage(text="Alex calling."),
    )
    await asyncio.wait_for(
        handle_answer(engine, supervisor, update_idle_timeout=idle), 1
    )
    engine.queue_node_opening.assert_not_awaited()
    assert engine.end_call_with_reason.call_args.args[0] == "screening_timeout"
    history = engine._gathered_context["answer_supervisor"]
    assert [entry["reason"] for entry in history] == ["screener", "screening_timeout"]
    assert history[-1] == {
        "action": "drop",
        "reason": "screening_timeout",
        "subtype": None,
        "screening_rearms": 1,
    }


@pytest.mark.asyncio
async def test_repeated_screeners_have_a_finite_budget():
    engine, supervisor, idle = make_call(
        [
            AnswerVerdict(
                AnswerAction.SCREEN_THEN_REARM, "screener", MachineSubtype.SCREENER
            ),
        ]
        * 3,
        screening_message=AnswerMessage(text="Alex calling."),
    )
    await asyncio.wait_for(
        handle_answer(engine, supervisor, update_idle_timeout=idle), 1
    )
    assert engine.task.queue_frame.await_count == 2
    assert engine.end_call_with_reason.call_args.args[0] == "screening_limit"
    history = engine._gathered_context["answer_supervisor"]
    assert [entry["screening_rearms"] for entry in history] == [0, 1, 2]
    assert all(entry["action"] == "screen_then_rearm" for entry in history)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message", [{}, {"text": "Do not play this."}, {"recording_pk": 9}]
)
async def test_voicemail_drop_verdict_ignores_message_and_records_drop(message):
    engine, supervisor, idle = make_call(
        [
            AnswerVerdict(AnswerAction.DROP, "voicemail", MachineSubtype.VOICEMAIL),
        ],
        voicemail_action="leave_message",
        voicemail_message=AnswerMessage(**message),
    )
    await asyncio.wait_for(
        handle_answer(engine, supervisor, update_idle_timeout=idle), 1
    )
    engine.task.queue_frame.assert_not_awaited()
    engine.wait_for_speech_playback.assert_not_awaited()
    engine.queue_node_opening.assert_not_awaited()
    assert engine.end_call_with_reason.call_args.args[0] == "voicemail_detected"
    assert engine._gathered_context["answer_supervisor"] == [
        {
            "action": "drop",
            "reason": "voicemail",
            "subtype": "VOICEMAIL",
            "screening_rearms": 0,
        }
    ]


@pytest.mark.asyncio
async def test_machine_timeout_disconnects_without_final_extraction():
    engine, supervisor, idle = make_call(
        [AnswerVerdict(AnswerAction.DROP, "machine_timeout")]
    )
    engine.end_call_with_reason = PipecatEngine.end_call_with_reason.__get__(engine)
    engine.perform_final_variable_extraction = AsyncMock()
    await asyncio.wait_for(
        handle_answer(engine, supervisor, update_idle_timeout=idle), 1
    )
    engine.perform_final_variable_extraction.assert_not_awaited()
    engine.queue_node_opening.assert_not_awaited()
    frame = engine.task.queue_frame.call_args.args[0]
    assert isinstance(frame, CancelFrame)
    assert frame.reason == "machine_timeout"
    assert engine._gathered_context["call_disposition"] == "machine_timeout"


@pytest.mark.asyncio
async def test_leave_message_policy_reports_missing_message_as_playback_failure():
    engine, supervisor, idle = make_call(
        [
            AnswerVerdict(
                AnswerAction.LEAVE_MESSAGE, "voicemail", MachineSubtype.VOICEMAIL
            ),
        ],
        voicemail_action="leave_message",
    )
    await asyncio.wait_for(
        handle_answer(engine, supervisor, update_idle_timeout=idle), 1
    )
    engine.task.queue_frame.assert_not_awaited()
    assert engine.end_call_with_reason.call_args.args[0] == "answer_message_failed"


@pytest.mark.asyncio
async def test_opening_error_cannot_leave_gate_and_idle_detection_disabled():
    engine, supervisor, idle = make_call(
        [
            AnswerVerdict(
                AnswerAction.RELEASE, "human_turn", MachineSubtype.CONVERSATION
            ),
        ]
    )
    engine.queue_node_opening = AsyncMock(
        side_effect=RuntimeError("recording unavailable")
    )
    await asyncio.wait_for(
        handle_answer(engine, supervisor, update_idle_timeout=idle), 1
    )
    supervisor.release.assert_called_once()
    assert [c.args[0] for c in idle.await_args_list] == [0, None]
    assert engine._queued_speech_mute_state == "idle"


@pytest.mark.asyncio
async def test_cancelled_pipeline_never_queues_an_opening():
    engine, supervisor, idle = make_call(
        [AnswerVerdict(AnswerAction.CANCELLED, "pipeline_ended")]
    )
    await asyncio.wait_for(
        handle_answer(engine, supervisor, update_idle_timeout=idle), 1
    )
    engine.queue_node_opening.assert_not_awaited()
    engine.task.queue_frame.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recording", [{"recording_id": "message-recording"}, {"recording_pk": 9}]
)
async def test_recording_message_uses_scoped_fetcher_and_transport(recording):
    engine, supervisor, idle = make_call(
        [
            AnswerVerdict(
                AnswerAction.LEAVE_MESSAGE, "voicemail", MachineSubtype.VOICEMAIL
            ),
        ],
        voicemail_action="leave_message",
        voicemail_message=AnswerMessage(**recording),
    )
    engine._fetch_recording_audio = AsyncMock(
        return_value=SimpleNamespace(audio=b"\0\0" * 160, transcript="Call back")
    )
    engine._transport_output = SimpleNamespace(queue_frame=AsyncMock())
    await asyncio.wait_for(
        handle_answer(engine, supervisor, update_idle_timeout=idle), 1
    )
    engine._fetch_recording_audio.assert_awaited_once_with(**recording)
    assert engine._transport_output.queue_frame.await_count > 0
    engine.task.queue_frame.assert_not_awaited()


@pytest.mark.asyncio
async def test_disconnect_during_playback_cancels_action_promptly():
    engine, supervisor, idle = make_call(
        [
            AnswerVerdict(
                AnswerAction.LEAVE_MESSAGE, "voicemail", MachineSubtype.VOICEMAIL
            ),
        ],
        voicemail_action="leave_message",
        voicemail_message=AnswerMessage(text="Call back."),
    )
    disconnected = asyncio.Event()
    supervisor.wait_closed = AsyncMock(side_effect=disconnected.wait)
    engine.wait_for_speech_playback = AsyncMock(side_effect=asyncio.Event().wait)
    running = asyncio.create_task(
        handle_answer(engine, supervisor, update_idle_timeout=idle)
    )
    async with asyncio.timeout(1):
        while not engine.wait_for_speech_playback.called:
            await asyncio.sleep(0)
    disconnected.set()
    await asyncio.wait_for(running, 1)
    engine.end_call_with_reason.assert_not_awaited()
    assert engine._queued_speech_mute_state == "idle"
