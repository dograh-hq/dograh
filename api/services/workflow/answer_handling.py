"""Engine actions for the answer supervisor; no pipeline policy in Pipecat."""

import asyncio
from copy import deepcopy
from typing import TYPE_CHECKING

from loguru import logger
from pipecat.processors.aggregators.llm_context import LLMContext

from api.enums import AnswerAction
from api.schemas.answer_supervisor import AnswerMessage
from api.services.pipecat.speech_playback import PlaybackOutcome, SpeechPlayback

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine import PipecatEngine

ANSWER_TERMINAL_REASONS = (
    "machine_timeout",
    "voicemail_no_message",
    "ivr_detected",
    "screening_timeout",
    "screening_limit",
    "screening_message_missing",
    "answer_message_failed",
)


async def _speak(
    engine: "PipecatEngine",
    message: AnswerMessage,
    *,
    mute_user: bool = True,
    append_to_context: bool = True,
) -> bool:
    """Use the existing org-scoped recording fetcher and transport playback tracker."""
    if not message.configured:
        return False
    append_to_context = append_to_context and not (
        message.recording_pk or message.recording_id
    )
    try:
        speech = await engine.queue_speech(
            **(
                {"recording_pk": message.recording_pk}
                if message.recording_pk
                else (
                    {"recording_id": message.recording_id}
                    if message.recording_id
                    else {"text": engine._format_prompt(message.text)}
                )
            ),
            mute_user=mute_user,
            append_to_context=append_to_context,
            persist_to_logs=not append_to_context,
        )
        return await speech.wait()
    except Exception:
        logger.exception("Answer-handling speech failed")
        return False


async def _speak_screening(engine: "PipecatEngine", supervisor) -> bool:
    """Play the screener's reply until it finishes or a human takes over."""

    async def accept_human():
        while True:
            verdict = await supervisor.wait_for_human()
            if supervisor.commit(verdict):
                return

    playback = asyncio.create_task(
        _speak(
            engine,
            supervisor.config.screening_message,
            mute_user=False,
            append_to_context=False,
        )
    )
    human = asyncio.create_task(accept_human())
    try:
        done, _ = await asyncio.wait(
            (playback, human), return_when=asyncio.FIRST_COMPLETED
        )
        if playback in done:
            return await playback
        await human
        # Stop preparation before interrupting so a pending recording fetch
        # cannot queue the screening reply after the human's greeting.
        playback.cancel()
        await asyncio.gather(playback, return_exceptions=True)
        return await engine.interrupt_screening_reply()
    finally:
        for task in (playback, human):
            task.cancel()
        await asyncio.gather(playback, human, return_exceptions=True)


async def _play_opening(
    engine, supervisor, *, provisional: bool, context=None
) -> SpeechPlayback | None:
    result = None
    try:
        async with asyncio.timeout(45):
            result = await engine.queue_node_opening(
                node_id=engine.active_agent.workflow.start_node_id,
                previous_node_id=None,
                generate_if_no_greeting=True,
                wait_for_playback=True,
                mute_user=False,
                opening_context=context,
            )
    except TimeoutError:
        logger.warning("Supervised opening timed out")
    except Exception as error:
        logger.warning("Supervised opening failed ({})", type(error).__name__)
    if provisional:
        supervisor.opening_finished()
    return result.playback if result is not None else None


async def _handle_answer(engine: "PipecatEngine", supervisor) -> AnswerAction | None:
    rearms = 0
    opening = None
    speech = None
    screening = False
    try:
        while not engine.is_call_disposed():
            verdict = await supervisor.wait_for_verdict()
            if verdict.action == AnswerAction.CANCELLED:
                return verdict.action
            if not supervisor.commit(verdict):
                continue
            engine._gathered_context.setdefault("answer_supervisor", []).append(
                {
                    **verdict.diagnostics,
                    "action": verdict.action.value,
                    "reason": verdict.reason,
                    "subtype": verdict.subtype.value if verdict.subtype else None,
                    "screening_rearms": rearms,
                }
            )
            if verdict.action == AnswerAction.START_OPENING:
                # Do not expose later unclassified speech (or workflow tools)
                # to the provisional opening's generation.
                context = LLMContext(messages=deepcopy(engine.context.messages))
                opening = asyncio.create_task(
                    _play_opening(engine, supervisor, provisional=True, context=context)
                )
                continue
            if opening is not None:
                # Wait for completion or interruption before changing playback.
                speech = await opening
            if verdict.action == AnswerAction.RELEASE:
                if opening is None:
                    speech = await _play_opening(engine, supervisor, provisional=False)
                interrupted = (
                    speech is not None and speech.outcome is PlaybackOutcome.INTERRUPTED
                )
                # Only cascade greetings install the temporary turn strategy.
                # Realtime caller turns are already committed before the verdict.
                if interrupted and not engine._is_realtime:
                    interrupted = await engine.greeting.wait_for_turn()
                # Commit queued caller speech before opening the inference gate.
                if not await engine.drain_call_pipeline():
                    await engine.end_call_with_reason(
                        "answer_message_failed", abort_immediately=True
                    )
                    return
                supervisor.release()
                engine.call_monitor.activate(waiting_for_user=True)
                # A pickup after screening is a new human turn. If an opening
                # already ran, answer that turn instead of waiting for another.
                if interrupted or (screening and opening is not None):
                    engine.expect_response()
                    await engine.active_agent.run_llm(engine.context)
                return

            if verdict.action == AnswerAction.WAIT_FOR_SCREENING:
                screening = True
                supervisor.begin_screening_wait()
                continue
            if verdict.action == AnswerAction.SCREEN_THEN_REARM:
                if rearms >= supervisor.config.max_screening_rearms:
                    reason = "screening_limit"
                elif not supervisor.config.screening_message.configured:
                    reason = "screening_message_missing"
                else:
                    screening = True
                    supervisor.begin_screening_wait(start_timeout=False)
                    if await _speak_screening(engine, supervisor):
                        rearms += 1
                        supervisor.start_screening_timeout()
                        continue
                    reason = "answer_message_failed"
            elif verdict.action == AnswerAction.LEAVE_MESSAGE:
                played = await _speak(engine, supervisor.config.voicemail_message)
                reason = "voicemail_detected" if played else "answer_message_failed"
            else:
                reason = {
                    "voicemail": "voicemail_detected",
                    "no_message": "voicemail_no_message",
                    "ivr": "ivr_detected",
                }.get(verdict.reason, verdict.reason)
            engine.set_call_disposition(reason)
            await engine.end_call_with_reason(reason, abort_immediately=True)
            return
    finally:
        if opening is not None:
            opening.cancel()
            await asyncio.gather(opening, return_exceptions=True)


async def handle_answer(
    engine: "PipecatEngine",
    supervisor,
):
    """Cancel pending inference/playback as soon as the pipeline ends."""
    actions = asyncio.create_task(_handle_answer(engine, supervisor))
    disconnected = asyncio.create_task(supervisor.wait_closed())
    cancelled = False
    try:
        done, _ = await asyncio.wait(
            (actions, disconnected), return_when=asyncio.FIRST_COMPLETED
        )
        if actions in done:
            cancelled = await actions == AnswerAction.CANCELLED
    finally:
        interrupted = not actions.done()
        for task in (actions, disconnected):
            task.cancel()
        await asyncio.gather(actions, disconnected, return_exceptions=True)
        if interrupted or cancelled:
            engine.speech_playback.cancel_all()
