"""Engine actions for the answer supervisor; no pipeline policy in Pipecat."""

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import TYPE_CHECKING

from loguru import logger
from pipecat.processors.aggregators.llm_context import LLMContext

from api.enums import AnswerAction
from api.schemas.answer_supervisor import AnswerMessage

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


async def _speak(engine: "PipecatEngine", message: AnswerMessage) -> bool:
    """Use the existing org-scoped recording fetcher and transport playback tracker."""
    if not message.configured:
        return False
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
            mute_user=True,
            append_to_context=not (message.recording_pk or message.recording_id),
            persist_to_logs=bool(message.recording_pk or message.recording_id),
        )
        return await speech.wait()
    except Exception:
        logger.exception("Answer-handling speech failed")
        return False


async def _play_opening(engine, supervisor, *, provisional: bool, context=None):
    try:
        async with asyncio.timeout(45):
            await engine.queue_node_opening(
                node_id=engine.active_agent.workflow.start_node_id,
                previous_node_id=None,
                generate_if_no_greeting=True,
                wait_for_playback=True,
                mute_user=not provisional,
                opening_context=context,
            )
    except TimeoutError:
        logger.warning("Supervised opening timed out")
    except Exception as error:
        logger.warning("Supervised opening failed ({})", type(error).__name__)
    if provisional:
        supervisor.opening_finished()


async def _stop_opening(engine, opening):
    # Cancel preparation first: a recording fetch must not enqueue the greeting
    # after the interruption. Cancelling a playback waiter does not stop audio.
    opening.cancel()
    await asyncio.gather(opening, return_exceptions=True)
    return await engine.interrupt_answer_opening()


async def _handle_answer(engine: "PipecatEngine", supervisor, update_idle_timeout):
    rearms = 0
    opening = None
    opening_stopped = False
    await update_idle_timeout(0)
    try:
        while not engine.is_call_disposed():
            verdict = await supervisor.wait_for_verdict()
            if verdict.action == AnswerAction.CANCELLED:
                return
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
            if verdict.action == AnswerAction.RELEASE:
                if opening is None:
                    await _play_opening(engine, supervisor, provisional=False)
                else:
                    # The initial greeting always finishes for a human answer.
                    # Node interruption policy applies after this handover.
                    await asyncio.gather(opening, return_exceptions=True)
                if opening is not None:
                    # Flush context writes while the gate is still closed.
                    # Playback consumed earlier triggers; only a turn completed
                    # after the greeting (or screening) needs an immediate reply.
                    if await engine.drain_call_pipeline():
                        run_llm = supervisor.has_pending_inference
                        supervisor.release()
                        if run_llm:
                            await engine.active_agent.run_llm(engine.context)
                    else:
                        await engine.end_call_with_reason(
                            "answer_message_failed", abort_immediately=True
                        )
                        return
                else:
                    supervisor.release()
                await update_idle_timeout(None)
                return

            if opening is not None and not opening_stopped:
                if not await _stop_opening(engine, opening):
                    await engine.end_call_with_reason(
                        "answer_message_failed", abort_immediately=True
                    )
                    return
                opening_stopped = True
            if verdict.action == AnswerAction.WAIT_FOR_SCREENING:
                supervisor.begin_screening_wait()
                continue
            if verdict.action == AnswerAction.SCREEN_THEN_REARM:
                if rearms >= supervisor.config.max_screening_rearms:
                    reason = "screening_limit"
                elif not supervisor.config.screening_message.configured:
                    reason = "screening_message_missing"
                elif await _speak(engine, supervisor.config.screening_message):
                    rearms += 1
                    supervisor.begin_screening_wait()
                    continue
                else:
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
    *,
    update_idle_timeout: Callable[[float | None], Awaitable[None]],
):
    """Cancel pending inference/playback as soon as the pipeline ends."""
    actions = asyncio.create_task(
        _handle_answer(engine, supervisor, update_idle_timeout)
    )
    disconnected = asyncio.create_task(supervisor.wait_closed())
    try:
        done, _ = await asyncio.wait(
            (actions, disconnected), return_when=asyncio.FIRST_COMPLETED
        )
        if actions in done:
            await actions
    finally:
        interrupted = not actions.done()
        for task in (actions, disconnected):
            task.cancel()
        await asyncio.gather(actions, disconnected, return_exceptions=True)
        if interrupted:
            engine.speech_playback.cancel_all()
