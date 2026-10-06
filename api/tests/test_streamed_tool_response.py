"""Replay streamed tool events through real adapters and the tool owner."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FunctionCallResultProperties,
    InterruptionFrame,
    LLMFullResponseStartFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
from pipecat.services.openai.live import events
from pipecat.utils.asyncio.task_manager import TaskManager

from api.services.workflow.pipecat_engine import PipecatEngine
from api.tests import test_realtime_conversation_contract as contract

realtime_service = contract.realtime_service
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.parametrize(
        "realtime_service", ["openai", "azure", "grok", "live"], indirect=True
    ),
]


def live_event(kind, *, response_id="response-1", **data):
    return events.ResponseEventEnvelope(
        type="response.event",
        delegation_id=response_id,
        event={"type": kind, **data},
    )


async def tool_event(service, name, call_id, *, response_id="response-1"):
    if type(service).__name__ == "DograhOpenAILiveLLMService":
        await service._handle_evt_response(
            live_event(
                "response.output_item.done",
                response_id=response_id,
                item={
                    "type": "function_call",
                    "status": "completed",
                    "call_id": call_id,
                    "name": name,
                    "arguments": "{}",
                },
            )
        )
    else:
        service._pending_function_calls[call_id] = SimpleNamespace(name=name)
        data = {"call_id": call_id, "name": name, "arguments": "{}"}
        if type(service).__name__ != "DograhGrokRealtimeLLMService":
            data["response_id"] = response_id
        await service._handle_evt_function_call_arguments_done(SimpleNamespace(**data))


async def response_done(service, status="completed", *, response_id="response-1"):
    if type(service).__name__ == "DograhOpenAILiveLLMService":
        await service._handle_evt_response(
            live_event(
                f"response.{status}",
                response_id=response_id,
                response={"status": status},
            )
        )
    else:
        await service._handle_evt_response_done(
            SimpleNamespace(
                usage=None,
                response=SimpleNamespace(
                    id=response_id,
                    status=status,
                    output=[],
                    status_details={"error": {"message": "test failure"}},
                    usage=SimpleNamespace(
                        input_tokens=0, output_tokens=0, total_tokens=0
                    ),
                ),
            )
        )


@pytest_asyncio.fixture
async def stream_engine(realtime_service, simple_workflow):
    service = realtime_service
    service._connect = AsyncMock()
    service._start_connecting = AsyncMock()
    service.broadcast_frame = AsyncMock()
    service._start_interruption = AsyncMock()
    service._handle_interruption = AsyncMock()
    service._handle_interruption_frame = AsyncMock()
    await service.setup(
        FrameProcessorSetup(
            clock=SystemClock(),
            task_manager=TaskManager(),
            pipeline_worker=SimpleNamespace(app_resources=None, worker_runner=None),
        )
    )
    engine = PipecatEngine(
        llm=service,
        workflow=simple_workflow,
        context=LLMContext(),
        call_context_vars={},
        workflow_run_id=123,
    )
    service._context = engine.context
    transitioned = asyncio.Event()

    async def save(params):
        await params.result_callback({"saved": True})

    async def end(params):
        engine._call_disposed = True
        transitioned.set()
        await params.result_callback(
            {"ended": True}, properties=FunctionCallResultProperties(run_llm=False)
        )

    service.register_function(
        "save_booking", engine.active_agent.bind_tool(engine, save)
    )
    service.register_function(
        "end_call",
        engine.active_agent.bind_tool(engine, end, is_node_transition=True),
        is_node_transition=True,
    )
    if type(service).__name__ == "DograhOpenAILiveLLMService":
        await service._handle_evt_response(live_event("response.created"))
    elif type(service).__name__ == "DograhGrokRealtimeLLMService":
        await service._handle_evt_response_created(
            SimpleNamespace(response=SimpleNamespace(id="response-1"))
        )
    yield engine, service, transitioned
    await service.cleanup()
    await asyncio.wait_for(engine.active_agent.finish_tool_calls(), 1)


@pytest.mark.parametrize("playback", ["silent", "stopped", "speaking"])
async def test_all_sibling_tools_are_accepted_before_transition(
    stream_engine, playback
):
    engine, service, transitioned = stream_engine
    if playback != "silent":
        await service.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    await tool_event(service, "end_call", "end-1")
    if playback == "stopped":
        await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    # Both ordinary tools arrive after the transition, one event at a time.
    for call_id in ("save-1", "save-2"):
        await tool_event(service, "save_booking", call_id)
        await asyncio.wait_for(engine.active_agent.finish_tool_calls(), 1)
        assert not transitioned.is_set()
    assert {r["tool_call_id"] for r in engine._gathered_context["tool_results"]} == {
        "save-1",
        "save-2",
    }
    await response_done(service)
    # A mixed response does not wait for playback, even while still speaking.
    await asyncio.wait_for(transitioned.wait(), 1)


@pytest.mark.parametrize("playback_first", [False, True])
async def test_single_transition_needs_response_end_and_playback_end(
    stream_engine, playback_first
):
    _engine, service, transitioned = stream_engine
    await service.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    await tool_event(service, "end_call", "end-1")
    if playback_first:
        await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    else:
        await response_done(service)
    await asyncio.sleep(0)
    assert not transitioned.is_set()
    if playback_first:
        await response_done(service)
    else:
        await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    await asyncio.wait_for(transitioned.wait(), 1)


async def test_interruption_discards_transition_before_response_finishes(stream_engine):
    _engine, service, transitioned = stream_engine
    await service.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    await tool_event(service, "end_call", "end-1")
    await service.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
    await response_done(service)
    await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    await asyncio.sleep(0)
    assert not transitioned.is_set()


async def test_failed_response_discards_transition(stream_engine):
    _engine, service, transitioned = stream_engine
    await service.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    await tool_event(service, "end_call", "end-1")
    await response_done(service, status="failed")
    await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    await asyncio.sleep(0)
    assert not transitioned.is_set()
    assert not service._workflow_tool_deferral.pending


async def test_voice_item_cannot_reset_response_tool_collection(stream_engine):
    _engine, service, transitioned = stream_engine
    await tool_event(service, "end_call", "end-1")
    await service._call_event_handler(
        "on_before_push_frame", LLMFullResponseStartFrame()
    )
    await service.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    await asyncio.sleep(0)
    assert not transitioned.is_set()
    await response_done(service)
    await asyncio.wait_for(transitioned.wait(), 1)


async def test_unrelated_response_end_cannot_release_transition(stream_engine):
    _engine, service, transitioned = stream_engine
    await tool_event(service, "end_call", "end-1")
    await response_done(service, response_id="older-response")
    await service.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    await asyncio.sleep(0)
    assert not transitioned.is_set()
    await response_done(service)
    await asyncio.wait_for(transitioned.wait(), 1)
