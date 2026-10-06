"""Simulate tool execution against call disposal and real LLM task cleanup."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import FunctionCallResultProperties
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameProcessorSetup
from pipecat.services.llm_service import FunctionCallFromLLM, FunctionCallParams
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.utils.asyncio.task_manager import TaskManager

from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager
from api.services.workflow.tools import custom_tool


@pytest_asyncio.fixture
async def running_engine(simple_workflow):
    with patch.object(OpenAILLMService, "create_client"):
        llm = OpenAILLMService(api_key="test-key")
    worker = SimpleNamespace(app_resources=None, worker_runner=None)
    await llm.setup(
        FrameProcessorSetup(
            clock=SystemClock(), task_manager=TaskManager(), pipeline_worker=worker
        )
    )
    llm.push_frame = AsyncMock()
    llm.broadcast_frame = AsyncMock()
    engine = PipecatEngine(
        llm=llm,
        workflow=simple_workflow,
        context=LLMContext(),
        call_context_vars={},
        workflow_run_id=123,
    )
    yield engine, llm
    await llm.cleanup()
    if hasattr(engine.active_agent, "finish_tool_calls"):
        await engine.active_agent.finish_tool_calls()


def call(engine, name, call_id="booking-1"):
    return FunctionCallFromLLM(
        context=engine.context,
        function_name=name,
        tool_call_id=call_id,
        arguments={"slot": "10:00"},
    )


def params(engine, name="save_booking", call_id="booking-1"):
    return FunctionCallParams(
        function_name=name,
        tool_call_id=call_id,
        arguments={"slot": "10:00"},
        context=engine.context,
        llm=engine.active_agent.llm,
        pipeline_worker=SimpleNamespace(app_resources=None),
        result_callback=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_started_save_survives_hangup_and_llm_cleanup(running_engine):
    engine, llm = running_engine
    started, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def save(p):
        started.set()
        await release.wait()
        completed.set()
        await p.result_callback({"saved": True})

    llm.register_function("save_booking", engine.active_agent.bind_tool(engine, save))
    await llm.run_function_calls([call(engine, "save_booking")])
    await asyncio.wait_for(started.wait(), 1)
    engine._call_disposed = True
    await llm.cleanup()
    release.set()
    await asyncio.wait_for(completed.wait(), 1)
    await engine.active_agent.finish_tool_calls()
    assert engine._gathered_context["tool_results"][0]["result"] == {"saved": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("transition_first", [False, True])
async def test_mixed_batch_saves_even_if_transition_cancels_llm_immediately(
    running_engine, transition_first
):
    engine, llm = running_engine
    ended, saved = asyncio.Event(), asyncio.Event()

    async def save(p):
        await ended.wait()
        saved.set()
        await p.result_callback({"saved": True})

    async def end(p):
        engine._call_disposed = True
        ended.set()
        await p.result_callback(
            {"ended": True}, properties=FunctionCallResultProperties(run_llm=False)
        )

    llm.register_function("save_booking", engine.active_agent.bind_tool(engine, save))
    llm.register_function(
        "end_call",
        engine.active_agent.bind_tool(engine, end, is_node_transition=True),
        is_node_transition=True,
    )
    calls = [call(engine, "save_booking"), call(engine, "end_call", "end-1")]
    await llm.run_function_calls(calls[::-1] if transition_first else calls)
    await asyncio.wait_for(ended.wait(), 1)
    await llm.cleanup()
    await asyncio.wait_for(saved.wait(), 1)
    await engine.active_agent.finish_tool_calls()
    assert [r["function_name"] for r in engine._gathered_context["tool_results"]] == [
        "save_booking"
    ]


@pytest.mark.asyncio
async def test_accepted_batch_survives_cleanup_before_runner_tasks_start(
    running_engine,
):
    engine, llm = running_engine
    saved = asyncio.Event()

    async def save(p):
        saved.set()
        await p.result_callback({"saved": True})

    llm.register_function("save_booking", engine.active_agent.bind_tool(engine, save))
    await llm.run_function_calls([call(engine, "save_booking")])
    engine._call_disposed = True
    await llm.cleanup()
    await asyncio.wait_for(saved.wait(), 1)
    await engine.active_agent.finish_tool_calls()
    assert engine._gathered_context["tool_results"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_transition_after_hangup_is_not_executed(running_engine):
    engine, _llm = running_engine
    handler = AsyncMock()
    bound = engine.active_agent.bind_tool(engine, handler, is_node_transition=True)
    engine._call_disposed = True
    p = params(engine, "end_call")
    await bound(p)
    handler.assert_not_awaited()
    p.result_callback.assert_awaited_once()
    assert p.result_callback.await_args.kwargs["properties"].run_llm is False


@pytest.mark.asyncio
@pytest.mark.parametrize("through_llm", [False, True])
async def test_new_ordinary_tool_after_hangup_is_not_accepted(
    running_engine, through_llm
):
    engine, llm = running_engine
    handler = AsyncMock()
    bound = engine.active_agent.bind_tool(engine, handler)
    llm.register_function("save_booking", bound)
    engine._call_disposed = True

    if through_llm:
        await llm.run_function_calls([call(engine, "save_booking")])
    else:
        await bound(params(engine))
    await asyncio.wait_for(engine.active_agent.finish_tool_calls(), 1)

    handler.assert_not_awaited()
    assert not engine._gathered_context.get("tool_results")


@pytest.mark.asyncio
async def test_hangup_cancels_running_transition_while_waiting_for_save(running_engine):
    engine, _llm = running_engine
    transition_started, save_started = asyncio.Event(), asyncio.Event()
    release_transition, release_save = asyncio.Event(), asyncio.Event()
    waiting_for_tools = asyncio.Event()
    transition_action = AsyncMock()
    original_finish = engine.finish_tool_calls

    async def finish():
        waiting_for_tools.set()
        await original_finish()

    async def transition(p):
        transition_started.set()
        await release_transition.wait()
        await transition_action()

    async def save(p):
        save_started.set()
        await release_save.wait()
        await p.result_callback({"saved": True})

    engine.finish_tool_calls = finish
    engine.perform_final_variable_extraction = AsyncMock()
    engine.retire_agents = AsyncMock()
    engine.call_worker = SimpleNamespace(queue_frame=AsyncMock())
    transition_task = asyncio.create_task(
        engine.active_agent.bind_tool(engine, transition, is_node_transition=True)(
            params(engine, "end_call", "end-1")
        )
    )
    save_task = asyncio.create_task(
        engine.active_agent.bind_tool(engine, save)(params(engine))
    )
    await asyncio.wait_for(transition_started.wait(), 1)
    await asyncio.wait_for(save_started.wait(), 1)
    ending = asyncio.create_task(
        engine.end_call_with_reason("user_hangup", abort_immediately=True)
    )
    try:
        await asyncio.wait_for(waiting_for_tools.wait(), 1)
        release_transition.set()
        await asyncio.wait_for(
            asyncio.gather(transition_task, return_exceptions=True), 1
        )
        transition_action.assert_not_awaited()
        assert not ending.done()
    finally:
        release_transition.set()
        release_save.set()
        await asyncio.wait_for(asyncio.gather(ending, save_task), 1)


@pytest.mark.asyncio
async def test_save_executes_once_and_keeps_normal_result_callback(running_engine):
    engine, _llm = running_engine
    executions = []

    async def save(p):
        executions.append(p.tool_call_id)
        await p.result_callback({"saved": True})

    bound = engine.active_agent.bind_tool(engine, save)
    p = params(engine)
    await asyncio.gather(bound(p), bound(p))
    assert executions == ["booking-1"]
    assert engine._gathered_context["tool_results"][0]["result"] == {"saved": True}
    p.result_callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_tool_timeout_is_bounded_and_recorded_after_hangup(running_engine):
    engine, _llm = running_engine
    started = asyncio.Event()

    async def save(p):
        started.set()
        await asyncio.Event().wait()

    bound = engine.active_agent.bind_tool(engine, save, timeout_secs=0.02)
    p = params(engine)
    task = asyncio.create_task(bound(p))
    await started.wait()
    engine._call_disposed = True
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.wait_for(engine.active_agent.finish_tool_calls(), 1)
    result = engine._gathered_context["tool_results"][0]
    assert result["status"] == "timeout"
    p.result_callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_connection_stays_open_until_tool_finishes(running_engine):
    engine, _llm = running_engine
    started, release = asyncio.Event(), asyncio.Event()
    session = SimpleNamespace(close_managed=AsyncMock())
    engine.active_agent.mcp_sessions["test"] = session

    async def save(p):
        started.set()
        await release.wait()
        session.close_managed.assert_not_awaited()
        await p.result_callback({"saved": True})

    bound = engine.active_agent.bind_tool(engine, save)
    task = asyncio.create_task(bound(params(engine)))
    await started.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    closing = asyncio.create_task(engine.active_agent.close_mcp_sessions())
    await asyncio.sleep(0)
    session.close_managed.assert_not_awaited()
    release.set()
    await asyncio.wait_for(closing, 1)
    session.close_managed.assert_awaited_once()


@pytest.mark.asyncio
async def test_hangup_waits_for_http_booking_before_final_extraction(
    running_engine, monkeypatch
):
    engine, llm = running_engine
    requested, release = asyncio.Event(), asyncio.Event()
    requests = []
    async_client = httpx.AsyncClient

    async def crm(request):
        requests.append(json.loads(request.content))
        requested.set()
        await release.wait()
        return httpx.Response(201, json={"booking_id": "test-booking-1"})

    monkeypatch.setattr(
        custom_tool.httpx,
        "AsyncClient",
        lambda **kwargs: async_client(transport=httpx.MockTransport(crm), **kwargs),
    )
    tool = SimpleNamespace(
        name="Save booking",
        tool_uuid="booking-tool",
        definition={
            "type": "http_api",
            "config": {"method": "POST", "url": "https://crm.example.com/bookings"},
        },
    )
    manager = CustomToolManager(engine)
    manager.get_organization_id = AsyncMock(return_value=1)
    handler = manager._create_http_tool_handler(tool, "save_booking")
    llm.register_function(
        "save_booking", engine.active_agent.bind_tool(engine, handler)
    )
    engine.perform_final_variable_extraction = AsyncMock()
    engine.retire_agents = AsyncMock()
    engine.call_worker = SimpleNamespace(queue_frame=AsyncMock())

    await llm.run_function_calls([call(engine, "save_booking")])
    await asyncio.wait_for(requested.wait(), 1)
    ending = asyncio.create_task(
        engine.end_call_with_reason("user_hangup", abort_immediately=True)
    )
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert engine.is_call_disposed()
        await llm.cleanup()
        engine.perform_final_variable_extraction.assert_not_awaited()
        engine.retire_agents.assert_not_awaited()
        assert not ending.done()
    finally:
        release.set()
        await asyncio.wait_for(ending, 1)
    assert requests == [{"slot": "10:00"}]
    result = engine._gathered_context["tool_results"][0]
    assert result["result"]["data"] == {"booking_id": "test-booking-1"}
    engine.perform_final_variable_extraction.assert_awaited_once()
