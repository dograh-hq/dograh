"""Replay tool batches and interruptions through every realtime wrapper."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    StopFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
from pipecat.services.llm_service import FunctionCallFromLLM, LLMService
from pipecat.utils.asyncio.task_manager import TaskManager

from api.tests import test_realtime_conversation_contract as conversation_contract

realtime_service = conversation_contract.realtime_service


@pytest_asyncio.fixture
async def tools_service(realtime_service, monkeypatch):
    service = realtime_service
    service._start_connecting = AsyncMock()
    await service.setup(
        FrameProcessorSetup(
            clock=SystemClock(),
            task_manager=TaskManager(),
            pipeline_worker=SimpleNamespace(app_resources=None, worker_runner=None),
        )
    )
    service._context = LLMContext()
    service._NODE_TRANSITION_TRANSCRIPTION_GRACE_SECONDS = 0
    service._start_interruption = AsyncMock()
    service._handle_interruption = AsyncMock()
    service._handle_interruption_frame = AsyncMock()
    service.stop_all_metrics = AsyncMock()
    service.stop = AsyncMock()
    service.cancel = AsyncMock()
    # Avoid provider sockets while preserving wrapper dispatch and frame handling.
    dispatch = AsyncMock()
    monkeypatch.setattr(LLMService, "run_function_calls", dispatch)
    for name in ("end_call", "next_node"):
        service.register_function(name, AsyncMock(), is_node_transition=True)
    service.register_function("save_booking", AsyncMock())
    service._bot_is_speaking = True
    service._bot_is_responding = True
    service._assistant_is_responding = True
    service._bot_responding = "voice"
    yield service, dispatch
    await service.cleanup()


def calls(service, names):
    return [
        FunctionCallFromLLM(
            context=service._context,
            function_name=name,
            tool_call_id=f"call-{name}",
            arguments={},
        )
        for name in names
    ]


async def submit(service, names):
    """Use each provider's actual tool-entry hook."""
    name = type(service).__name__
    batch = calls(service, names)
    if "Gemini" in name:
        await service._run_or_defer_function_calls(batch)
    elif "Ultravox" in name:
        for fc in batch:
            await service._handle_tool_invocation(fc.function_name, fc.tool_call_id, {})
    elif "Realtime" in name and "AWS" not in name:
        for fc in batch:
            service._pending_function_calls[fc.tool_call_id] = SimpleNamespace(
                name=fc.function_name
            )
            await service._handle_evt_function_call_arguments_done(
                SimpleNamespace(
                    name=fc.function_name,
                    call_id=fc.tool_call_id,
                    response_id="response-1",
                    arguments="{}",
                )
            )
    else:
        await service.run_function_calls(batch)


def dispatched_names(dispatch):
    return [
        fc.function_name
        for invocation in dispatch.await_args_list
        for fc in invocation.args[0]
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "names",
    [
        ["save_booking"],
        ["end_call", "save_booking"],
        ["save_booking", "end_call"],
        ["end_call", "next_node"],
    ],
)
async def test_ordinary_and_multiple_tools_run_without_playback(tools_service, names):
    service, dispatch = tools_service
    await submit(service, names)
    assert dispatched_names(dispatch) == names
    await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    assert dispatched_names(dispatch) == names


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "interrupt",
    [
        InterruptionFrame,
        lambda: BotStoppedSpeakingFrame(interrupted=True),
        CancelFrame,
        EndFrame,
        StopFrame,
    ],
)
async def test_interrupted_transition_never_runs_after_later_stop(
    tools_service, interrupt
):
    service, dispatch = tools_service
    await submit(service, ["end_call"])
    dispatch.assert_not_awaited()
    await service.process_frame(interrupt(), FrameDirection.DOWNSTREAM)
    await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    task = getattr(service, "_transition_function_call_task", None)
    if task:
        await asyncio.wait_for(task, 1)
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_normal_stop_runs_a_single_transition_once(tools_service):
    service, dispatch = tools_service
    await submit(service, ["end_call"])
    dispatch.assert_not_awaited()
    await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    task = getattr(service, "_transition_function_call_task", None)
    if task:
        await asyncio.wait_for(task, 1)
    assert dispatched_names(dispatch) == ["end_call"]
    await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    assert dispatched_names(dispatch) == ["end_call"]


@pytest.mark.asyncio
async def test_provider_originated_interruption_drops_transition(tools_service):
    service, dispatch = tools_service
    await submit(service, ["end_call"])
    await service.broadcast_interruption()
    await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    task = getattr(service, "_transition_function_call_task", None)
    if task:
        await asyncio.wait_for(task, 1)
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "realtime_service", ["gemini", "vertex", "nova", "ultravox"], indirect=True
)
async def test_provider_turn_end_cannot_release_before_playback(tools_service):
    service, dispatch = tools_service
    name = type(service).__name__
    await submit(service, ["end_call"])
    if "Gemini" in name:
        await service._set_bot_is_responding(False)
    elif "Nova" in name:
        service._assistant_is_responding = False
        await service._report_assistant_response_ended()
    else:
        await service._handle_response_end()
    task = getattr(service, "_transition_function_call_task", None)
    if task:
        await asyncio.wait_for(task, 1)
    dispatch.assert_not_awaited()
    await service.process_frame(
        BotStoppedSpeakingFrame(interrupted=True), FrameDirection.UPSTREAM
    )
    await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "names", [["end_call", "save_booking"], ["save_booking", "end_call"]]
)
async def test_separate_tool_events_release_transition_in_either_order(
    tools_service, names
):
    service, dispatch = tools_service
    await submit(service, names[:1])
    await submit(service, names[1:])
    assert dispatched_names(dispatch) == names


@pytest.mark.asyncio
@pytest.mark.parametrize("realtime_service", ["gemini", "vertex"], indirect=True)
async def test_gemini_grace_period_cannot_resurrect_interrupted_transition(
    tools_service,
):
    service, dispatch = tools_service
    service._NODE_TRANSITION_TRANSCRIPTION_GRACE_SECONDS = 0.02
    await submit(service, ["end_call"])
    await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    task = service._transition_function_call_task
    await service.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
    await asyncio.wait_for(task, 1)
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("realtime_service", ["gemini", "vertex"], indirect=True)
async def test_gemini_second_tool_releases_transition_waiting_for_transcription(
    tools_service,
):
    service, dispatch = tools_service
    service._NODE_TRANSITION_TRANSCRIPTION_GRACE_SECONDS = 0.05
    await submit(service, ["end_call"])
    await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    await submit(service, ["save_booking"])
    assert dispatched_names(dispatch) == ["end_call", "save_booking"]


@pytest.mark.asyncio
async def test_new_response_can_defer_its_own_single_transition(tools_service):
    service, dispatch = tools_service
    await submit(service, ["save_booking"])
    dispatch.reset_mock()
    if type(service).__name__ == "DograhOpenAILiveLLMService":
        await service._handle_evt_response(
            SimpleNamespace(
                inner_type="response.created",
                delegation_id="new-response",
            )
        )
    else:
        await service._call_event_handler(
            "on_before_push_frame", LLMFullResponseEndFrame()
        )
        await service._call_event_handler(
            "on_before_push_frame", LLMFullResponseStartFrame()
        )
    await submit(service, ["end_call"])
    dispatch.assert_not_awaited()
    await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    task = getattr(service, "_transition_function_call_task", None)
    if task:
        await asyncio.wait_for(task, 1)
    assert dispatched_names(dispatch) == ["end_call"]


@pytest.mark.asyncio
@pytest.mark.parametrize("realtime_service", ["live"], indirect=True)
async def test_live_voice_response_does_not_discard_backend_tool(tools_service):
    service, dispatch = tools_service
    await submit(service, ["end_call"])
    await service._call_event_handler(
        "on_before_push_frame", LLMFullResponseStartFrame()
    )
    await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    assert dispatched_names(dispatch) == ["end_call"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "realtime_service", ["gemini", "vertex", "nova", "ultravox"], indirect=True
)
async def test_transition_arriving_after_generation_waits_for_remaining_audio(
    tools_service,
):
    service, dispatch = tools_service
    await service.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    service._bot_is_responding = False
    service._assistant_is_responding = False
    service._bot_responding = None
    await submit(service, ["end_call"])
    task = getattr(service, "_transition_function_call_task", None)
    if task:
        await asyncio.wait_for(task, 1)
    dispatch.assert_not_awaited()
    await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    task = getattr(service, "_transition_function_call_task", None)
    if task:
        await asyncio.wait_for(task, 1)
    assert dispatched_names(dispatch) == ["end_call"]


@pytest.mark.asyncio
async def test_fresh_response_after_interruption_forgets_previous_tool_count(
    tools_service,
):
    service, dispatch = tools_service
    await service._call_event_handler(
        "on_before_push_frame", LLMFullResponseStartFrame()
    )
    await submit(service, ["save_booking", "end_call"])
    await service.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
    dispatch.reset_mock()
    # An interrupted response may never deliver its end frame.
    if type(service).__name__ == "DograhOpenAILiveLLMService":
        await service._handle_evt_response(
            SimpleNamespace(
                inner_type="response.created",
                delegation_id="new-response",
            )
        )
    else:
        await service._call_event_handler(
            "on_before_push_frame", LLMFullResponseStartFrame()
        )
    await service.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    await submit(service, ["next_node"])
    dispatch.assert_not_awaited()
