"""Engine contracts: one opening, bounded playback, and silent screening waits."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pipecat.frames.frames import (
    CancelFrame,
    InterruptionFrame,
    LLMContextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from api.enums import AnswerAction
from api.schemas.answer_supervisor import AnswerMessage, AnswerSupervisorConfig
from api.services.pipecat.answer_classification import MachineSubtype
from api.services.pipecat.processors.answer_supervisor import (
    AnswerSupervisor,
    AnswerVerdict,
)
from api.services.pipecat.speech_playback import PlaybackOutcome
from api.services.workflow.answer_handling import _speak_screening, handle_answer
from api.services.workflow.pipecat_engine import NodeOpeningResult, PipecatEngine


def make_call(verdicts, **settings):
    engine = PipecatEngine(workflow=None, call_context_vars={}, workflow_run_id=1)
    engine.active_agent.workflow = SimpleNamespace(start_node_id="start")
    engine.call_worker = SimpleNamespace(queue_frame=AsyncMock())
    engine.queue_node_opening = AsyncMock(return_value=NodeOpeningResult("greeting"))
    engine.drain_call_pipeline = AsyncMock(return_value=True)
    engine.end_call_with_reason = AsyncMock()
    engine.playback_wait = AsyncMock(return_value=True)
    queue_speech = engine.queue_speech

    async def queue(*args, **kwargs):
        speech = await queue_speech(*args, **kwargs)
        if not speech.done:

            async def wait():
                played = await engine.playback_wait()
                speech.finish(
                    PlaybackOutcome.PLAYED if played else PlaybackOutcome.FAILED
                )
                return played

            speech.wait = wait
        return speech

    engine.queue_speech = queue
    supervisor = Mock()
    supervisor.config = AnswerSupervisorConfig(**settings)
    supervisor.wait_for_verdict = AsyncMock(side_effect=verdicts)
    supervisor.wait_for_human = AsyncMock(side_effect=asyncio.Event().wait)
    supervisor.close = AsyncMock()
    supervisor.wait_closed = AsyncMock(side_effect=asyncio.Event().wait)
    activation = Mock(wraps=engine.call_monitor.activate)
    engine.call_monitor.activate = activation
    return engine, supervisor, activation


@pytest.mark.asyncio
async def test_only_accepted_decision_and_its_transcript_enter_gathered_context():
    engine, _, idle = make_call([])
    supervisor = AnswerSupervisor(AnswerSupervisorConfig(), context=LLMContext())
    try:
        # A result can be revoked while the engine is still fetching pre-call data.
        await supervisor._classify_turn("Please leave a message after the tone.", 0)
        supervisor._speech_started()
        await supervisor._on_turn_stopped(
            None, "external_turn", SimpleNamespace(content="Hello, this is Alex.")
        )
        await asyncio.wait_for(handle_answer(engine, supervisor), 1)

        [decision] = engine._gathered_context["answer_supervisor"]
        assert decision["action"] == "release"
        assert decision["reason"] == "human_turn"
        assert decision["subtype"] == "CONVERSATION"
        assert decision["transcript"] == "Hello, this is Alex."
        assert decision["strategy"] == "duration_threshold"
        assert decision["pattern_subtype"] == "UNKNOWN"
        assert decision["human_utterance_max_ms"] == 2500
        assert decision["duration_ms"] is not None
        assert decision["timestamp"]
        assert "event" not in decision
        engine.end_call_with_reason.assert_not_awaited()
    finally:
        await supervisor.close()


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
    engine.playback_wait = AsyncMock(side_effect=playback.wait)
    running = asyncio.create_task(handle_answer(engine, supervisor))
    async with asyncio.timeout(1):
        while not engine.playback_wait.called:
            await asyncio.sleep(0)
    engine.end_call_with_reason.assert_not_awaited()
    frame = next(
        call.args[0]
        for call in engine.call_worker.queue_frame.await_args_list
        if isinstance(call.args[0], TTSSpeakFrame)
    )
    assert isinstance(frame, TTSSpeakFrame)
    assert frame.text == "Please call us back."
    assert frame.append_to_context is True
    assert frame.persist_to_logs is False
    playback.set()
    await asyncio.wait_for(running, 1)
    assert engine.end_call_with_reason.call_args.args[0] == "voicemail_detected"
    engine.queue_node_opening.assert_not_awaited()
    idle.assert_not_called()
    assert not engine.call_monitor.active


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
    await asyncio.wait_for(handle_answer(engine, supervisor), 1)
    supervisor.begin_screening_wait.assert_called_once()
    supervisor.release.assert_called_once()
    engine.queue_node_opening.assert_awaited_once()
    idle.assert_called_once_with(waiting_for_user=True)
    assert engine._mute_pipeline is False
    assert not engine.speech_playback.mutes_user
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
    await asyncio.wait_for(handle_answer(engine, supervisor), 1)
    idle.assert_not_called()
    assert not engine.call_monitor.active
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
    await asyncio.wait_for(handle_answer(engine, supervisor), 1)
    assert (
        sum(
            isinstance(c.args[0], TTSSpeakFrame)
            for c in engine.call_worker.queue_frame.await_args_list
        )
        == 2
    )
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
    await asyncio.wait_for(handle_answer(engine, supervisor), 1)
    engine.call_worker.queue_frame.assert_not_awaited()
    engine.playback_wait.assert_not_awaited()
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
    await asyncio.wait_for(handle_answer(engine, supervisor), 1)
    engine.perform_final_variable_extraction.assert_not_awaited()
    engine.queue_node_opening.assert_not_awaited()
    frame = engine.call_worker.queue_frame.call_args.args[0]
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
    await asyncio.wait_for(handle_answer(engine, supervisor), 1)
    engine.call_worker.queue_frame.assert_not_awaited()
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
    await asyncio.wait_for(handle_answer(engine, supervisor), 1)
    supervisor.release.assert_called_once()
    idle.assert_called_once_with(waiting_for_user=True)
    assert not engine.speech_playback.mutes_user


@pytest.mark.asyncio
@pytest.mark.parametrize("greeting_type", ["text", "audio", "llm"])
@pytest.mark.parametrize("interrupted", [False, True])
async def test_realtime_provisional_opening_answers_held_turn_after_interruption(
    simple_workflow, greeting_type, interrupted
):
    node = simple_workflow.nodes[simple_workflow.start_node_id]
    node.greeting_type = greeting_type
    node.greeting = "Welcome." if greeting_type == "text" else None
    node.greeting_recording_id = "9" if greeting_type == "audio" else None
    context = LLMContext()
    llm = SimpleNamespace(queue_frame=AsyncMock())
    engine = PipecatEngine(
        workflow=simple_workflow,
        llm=llm,
        context=context,
        call_context_vars={},
        is_realtime=True,
    )
    engine.call_worker = SimpleNamespace(queue_frame=AsyncMock())
    engine.set_transport_output(SimpleNamespace(queue_frame=AsyncMock()))
    engine.set_fetch_recording_audio(
        AsyncMock(
            return_value=SimpleNamespace(audio=b"\x01\x00" * 160, transcript="Welcome.")
        )
    )
    supervisor = AnswerSupervisor(AnswerSupervisorConfig(), context=context)

    async def drain():
        assert supervisor.blocks_workflow
        assert context.messages == [{"role": "user", "content": "Hello, can you help?"}]
        return True

    engine.drain_call_pipeline = AsyncMock(side_effect=drain)
    supervisor._publish(
        AnswerVerdict(AnswerAction.START_OPENING, "silent_window"),
        strategy="listening_timeout",
    )
    running = asyncio.create_task(handle_answer(engine, supervisor))
    try:
        async with asyncio.timeout(1):
            while not engine.speech_playback.pending:
                await asyncio.sleep(0)
        [speech] = engine.speech_playback.pending.values()
        assert not engine.call_monitor.active
        assert engine.speech_playback.greeting is None
        # The provisional generation, if any, must not be counted as the reply.
        llm.queue_frame.reset_mock()

        async def followup(_frame):
            assert engine.call_monitor.active

        llm.queue_frame.side_effect = followup
        supervisor._speech_started()
        if interrupted:
            await engine.speech_playback.before_output(None, InterruptionFrame())
            await asyncio.sleep(0)
            assert supervisor.blocks_workflow
            llm.queue_frame.assert_not_awaited()
        context.add_message({"role": "user", "content": "Hello, can you help?"})
        await supervisor.llm_gate().process_frame(
            LLMContextFrame(context), FrameDirection.DOWNSTREAM
        )
        assert supervisor.llm_gate().dropped_contexts == 1
        await supervisor._on_turn_stopped(
            None, "external_turn", SimpleNamespace(content="Hello, can you help?")
        )
        if not interrupted:
            speech.finish(PlaybackOutcome.PLAYED)
        await asyncio.wait_for(running, 1)

        assert not supervisor.blocks_workflow
        assert engine.call_monitor.active
        engine.drain_call_pipeline.assert_awaited_once()
        if interrupted:
            llm.queue_frame.assert_awaited_once()
            [frame] = llm.queue_frame.await_args.args
            assert isinstance(frame, LLMContextFrame)
            assert frame.context is context
        else:
            llm.queue_frame.assert_not_awaited()
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        engine.speech_playback.cancel_all()
        await supervisor.close()


@pytest.mark.asyncio
async def test_cancelled_pipeline_never_queues_an_opening():
    engine, supervisor, idle = make_call(
        [AnswerVerdict(AnswerAction.CANCELLED, "pipeline_ended")]
    )
    await asyncio.wait_for(handle_answer(engine, supervisor), 1)
    engine.queue_node_opening.assert_not_awaited()
    engine.call_worker.queue_frame.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("shutdown", ["cancelled_verdict", "disconnect", "parent"])
async def test_shutdown_cancels_pending_opening_playback(shutdown):
    engine, supervisor, idle = make_call([])
    engine.context = LLMContext()
    verdicts = asyncio.Queue()
    verdicts.put_nowait(AnswerVerdict(AnswerAction.START_OPENING, "silent_window"))
    supervisor.wait_for_verdict = AsyncMock(side_effect=verdicts.get)
    disconnected = asyncio.Event()
    supervisor.wait_closed = AsyncMock(side_effect=disconnected.wait)
    opening_queued = asyncio.get_running_loop().create_future()

    async def play_opening(**kwargs):
        # Use the real playback handle: cancelling its waiter does not finish it.
        speech = await PipecatEngine.queue_speech(
            engine, text="Hello.", mute_user=kwargs["mute_user"]
        )
        opening_queued.set_result(speech)
        await speech.wait()

    engine.queue_node_opening = AsyncMock(side_effect=play_opening)
    running = asyncio.create_task(handle_answer(engine, supervisor))
    try:
        speech = await asyncio.wait_for(opening_queued, 1)
        assert not speech.done
        if shutdown == "cancelled_verdict":
            # Exercise normal action completion, without the disconnect waiter
            # winning the race and cancelling the action task instead.
            verdicts.put_nowait(AnswerVerdict(AnswerAction.CANCELLED, "pipeline_ended"))
        elif shutdown == "disconnect":
            disconnected.set()
        else:
            running.cancel()

        if shutdown == "parent":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(running, 1)
        else:
            await asyncio.wait_for(running, 1)

        assert speech.outcome is PlaybackOutcome.CLOSED
        assert not engine.speech_playback.pending
        assert not engine.speech_playback.mutes_user
        supervisor.release.assert_not_called()
        engine.end_call_with_reason.assert_not_awaited()
    finally:
        running.cancel()
        await asyncio.wait_for(asyncio.gather(running, return_exceptions=True), 1)
        engine.speech_playback.cancel_all()


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
    await asyncio.wait_for(handle_answer(engine, supervisor), 1)
    engine._fetch_recording_audio.assert_awaited_once_with(**recording)
    assert engine._transport_output.queue_frame.await_count > 0
    engine.call_worker.queue_frame.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action", [AnswerAction.LEAVE_MESSAGE, AnswerAction.SCREEN_THEN_REARM]
)
async def test_disconnect_during_playback_cancels_action_promptly(action):
    engine, supervisor, idle = make_call(
        [
            AnswerVerdict(
                action,
                "voicemail" if action == AnswerAction.LEAVE_MESSAGE else "screener",
                (
                    MachineSubtype.VOICEMAIL
                    if action == AnswerAction.LEAVE_MESSAGE
                    else MachineSubtype.SCREENER
                ),
            ),
        ],
        voicemail_action="leave_message",
        voicemail_message=AnswerMessage(text="Call back."),
        screening_message=AnswerMessage(text="Alex calling."),
    )
    disconnected = asyncio.Event()
    supervisor.wait_closed = AsyncMock(side_effect=disconnected.wait)
    engine.playback_wait = AsyncMock(side_effect=asyncio.Event().wait)
    running = asyncio.create_task(handle_answer(engine, supervisor))
    async with asyncio.timeout(1):
        while not engine.playback_wait.called:
            await asyncio.sleep(0)
    disconnected.set()
    await asyncio.wait_for(running, 1)
    engine.end_call_with_reason.assert_not_awaited()
    assert not engine.speech_playback.mutes_user


@pytest.mark.asyncio
async def test_human_pickup_cancels_screening_recording_preparation():
    engine, supervisor, _ = make_call(
        [], screening_message=AnswerMessage(recording_pk=10)
    )
    fetching, cancelled, picked_up = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def fetch(**_kwargs):
        fetching.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def human():
        await picked_up.wait()
        return AnswerVerdict(
            AnswerAction.RELEASE, "human_turn", MachineSubtype.CONVERSATION
        )

    engine._fetch_recording_audio = AsyncMock(side_effect=fetch)
    engine._transport_output = SimpleNamespace(queue_frame=AsyncMock())
    engine.interrupt_screening_reply = AsyncMock(return_value=True)
    supervisor.wait_for_human = AsyncMock(side_effect=human)
    playing = asyncio.create_task(_speak_screening(engine, supervisor))
    try:
        await asyncio.wait_for(fetching.wait(), 1)
        picked_up.set()
        assert await asyncio.wait_for(playing, 1)
        assert cancelled.is_set()
        engine.interrupt_screening_reply.assert_awaited_once()
        engine.call_worker.queue_frame.assert_not_awaited()
        engine._transport_output.queue_frame.assert_not_awaited()
        assert not engine.speech_playback.pending
    finally:
        playing.cancel()
        await asyncio.gather(playing, return_exceptions=True)
