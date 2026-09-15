"""An end-node tool result must not close a still-producing voice session."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.utils.enums import EndTaskReason

from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.text_chat_runner import (
    _ResponseWindowState,
    _TextChatCaptureProcessor,
)


@pytest.fixture
def engine(three_node_workflow_no_variable_extraction):
    workflow = three_node_workflow_no_variable_extraction
    engine = PipecatEngine(
        task=SimpleNamespace(queue_frame=AsyncMock()),
        workflow=workflow,
        call_context_vars={},
    )
    engine.active_agent.current_node = workflow.nodes["agent"]
    engine.set_node = AsyncMock()
    engine._perform_variable_extraction_if_needed = AsyncMock()
    engine.perform_final_variable_extraction = AsyncMock()
    return engine


async def transition_to_end(engine):
    transition = await engine._create_transition_func("move_to_end_call", "end")
    result = AsyncMock()
    await transition(SimpleNamespace(arguments={}, result_callback=result))
    assert result.await_args.args == ({"status": "done"},)
    return result.await_args.kwargs["properties"].on_context_updated


@pytest.mark.asyncio
@pytest.mark.parametrize("is_realtime", [True, False])
@pytest.mark.parametrize("playback", ["delayed", "already_started", "already_finished"])
async def test_end_node_waits_for_goodbye_playback(engine, is_realtime, playback):
    engine._is_realtime = is_realtime
    # Old playback must not count as the end node's goodbye.
    await engine.should_mute_user(BotStartedSpeakingFrame())
    await engine.should_mute_user(BotStoppedSpeakingFrame())
    finish = await transition_to_end(engine)
    assert engine._mute_pipeline

    # Realtime may start (or finish) before the context callback is scheduled.
    if playback != "delayed":
        await engine.should_mute_user(BotStartedSpeakingFrame())
    if playback == "already_finished":
        await engine.should_mute_user(BotStoppedSpeakingFrame())

    finishing = asyncio.create_task(finish())
    try:
        if playback != "already_finished":
            await asyncio.sleep(0)
            engine.call_worker.queue_frame.assert_not_awaited()
            if playback == "delayed":
                await engine.should_mute_user(BotStartedSpeakingFrame())
            # Finishing inference does not mean the audio has reached the caller.
            await engine.should_mute_user(LLMFullResponseEndFrame())
            await asyncio.sleep(0)
            engine.call_worker.queue_frame.assert_not_awaited()
            engine.perform_final_variable_extraction.assert_not_awaited()
            await engine.should_mute_user(BotStoppedSpeakingFrame())

        await asyncio.wait_for(finishing, timeout=1)
        engine.perform_final_variable_extraction.assert_awaited_once()
        terminal = engine.call_worker.queue_frame.await_args.args[0]
        assert isinstance(terminal, EndFrame)
        assert terminal.reason == EndTaskReason.END_CALL.value
        assert not engine._bot_is_speaking
    finally:
        if not finishing.done():
            finishing.cancel()
            await asyncio.gather(finishing, return_exceptions=True)


@pytest.mark.asyncio
async def test_caller_hangup_can_cancel_while_goodbye_is_pending(engine):
    finish = await transition_to_end(engine)
    finishing = asyncio.create_task(finish())
    try:
        await asyncio.sleep(0)
        await engine.end_call_with_reason(
            EndTaskReason.USER_HANGUP.value, abort_immediately=True
        )
        terminal = engine.call_worker.queue_frame.await_args.args[0]
        assert isinstance(terminal, CancelFrame)
        assert terminal.reason == EndTaskReason.USER_HANGUP.value
    finally:
        finishing.cancel()
        await asyncio.gather(finishing, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("starts", [False, True])
async def test_end_node_wait_is_bounded_when_audio_stalls(engine, starts):
    finish = await transition_to_end(engine)
    original_wait = engine.wait_for_speech_playback

    async def short_wait():
        return await original_wait(start_timeout=0.01, playback_timeout=0.01)

    engine.wait_for_speech_playback = short_wait
    if starts:
        await engine.should_mute_user(BotStartedSpeakingFrame())
    await asyncio.wait_for(finish(), timeout=1)
    assert isinstance(engine.call_worker.queue_frame.await_args.args[0], EndFrame)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["static", "llm", "recording"])
async def test_text_delivery_completes_the_shared_playback_wait(engine, source):
    context = LLMContext()
    window = _ResponseWindowState()
    capture = _TextChatCaptureProcessor(window, context, engine)
    capture.push_frame = AsyncMock()
    engine.arm_speech_playback()
    waiting = asyncio.create_task(engine.wait_for_speech_playback())
    try:
        if source == "static":
            await capture.process_frame(
                TTSSpeakFrame("Goodbye!"), FrameDirection.DOWNSTREAM
            )
            assert window.outputs == ["Goodbye!"]
        else:
            start = (
                LLMFullResponseStartFrame() if source == "llm" else TTSStartedFrame()
            )
            stop = LLMFullResponseEndFrame() if source == "llm" else TTSStoppedFrame()
            await capture.process_frame(start, FrameDirection.DOWNSTREAM)
            await asyncio.sleep(0)
            assert not waiting.done()
            await capture.process_frame(stop, FrameDirection.DOWNSTREAM)
        assert await asyncio.wait_for(waiting, timeout=1)
    finally:
        if not waiting.done():
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
