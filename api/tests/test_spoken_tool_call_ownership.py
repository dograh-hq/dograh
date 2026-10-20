"""Replay recovered batches through providers and the workflow's tool owner."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from google.genai.types import FinishReason, FunctionCall, Part
from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    FunctionCallResultProperties,
    FunctionCallsStartedFrame,
    InterruptionFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
from pipecat.utils.asyncio.task_manager import TaskManager

from api.services.pipecat.service_factory import (
    DograhGoogleLLMService,
    DograhLLMService,
)
from api.services.workflow.pipecat_engine import PipecatEngine
from api.tests import test_fallback_llm_processor as fallback
from api.tests import test_gemini_spoken_tool_calls as gemini
from api.tests import test_openai_spoken_tool_calls as openai

harness = fallback.harness


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["dograh", "gemini"])
@pytest.mark.parametrize("recovered", ["save", "end_call", "both"])
@pytest.mark.parametrize("ending", ["playback", "interruption", "new_response"])
async def test_goodbye_accepts_saves_before_deferring_result_delivery(
    simple_workflow, provider, recovered, ending
):
    service_class = DograhLLMService if provider == "dograh" else DograhGoogleLLMService
    service = service_class(api_key="offline-key")
    manager = TaskManager()
    await service.setup(
        FrameProcessorSetup(
            clock=SystemClock(),
            task_manager=manager,
            pipeline_worker=SimpleNamespace(app_resources={}, worker_runner=None),
        )
    )
    service.push_error = AsyncMock()
    frames, invocations = [], []
    release, results_delivered = asyncio.Event(), asyncio.Event()

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        frames.append(frame)

    async def broadcast(cls, **kwargs):
        frames.append(cls(**kwargs))
        if len([f for f in frames if isinstance(f, FunctionCallResultFrame)]) == 2:
            results_delivered.set()

    service.push_frame = push
    service.broadcast_frame = broadcast
    engine = PipecatEngine(
        llm=service,
        workflow=simple_workflow,
        context=gemini.context(),
        call_context_vars={},
        workflow_run_id=123,
    )

    async def save(params):
        invocations.append((params.function_name, params.arguments))
        await asyncio.wait_for(release.wait(), 2)
        await params.result_callback({"saved": True})

    async def end(params):
        invocations.append((params.function_name, params.arguments))
        await params.result_callback(
            {"ended": True}, properties=FunctionCallResultProperties(run_llm=False)
        )

    service.register_function("save", engine.active_agent.bind_tool(engine, save))
    service.register_function(
        "end_call",
        engine.active_agent.bind_tool(engine, end, is_node_transition=True),
        is_node_transition=True,
    )
    text = "Goodbye! "
    native_calls = []
    for name, args in [("save", {"reason": "tomorrow"}), ("end_call", {})]:
        if recovered in (name, "both"):
            text += (
                'default_api:save{"reason":"tomorrow"} '
                if name == "save"
                else "default_api:end_call{}"
            )
        elif provider == "dograh":
            native_calls.append(
                openai.chunk(
                    tools=[
                        openai.native(name, '{"reason":"tomorrow"}' if args else "{}")
                    ]
                )
            )
        else:
            native_calls.append(
                gemini.chunk(
                    Part(function_call=FunctionCall(id="native", name=name, args=args))
                )
            )
    if provider == "dograh":
        openai.install_stream(service, [text, *native_calls], finish="tool_calls")
    else:
        gemini.install_stream(service, [text, *native_calls])

    try:
        await service._process_context(engine.context)
        service.push_error.assert_not_awaited()
        speech = "".join(f.text for f in frames if isinstance(f, LLMTextFrame))
        assert speech.strip() == "Goodbye!"
        assert not any(name == "end_call" for name, _ in invocations)

        if ending == "interruption":
            # Even immediate interruption/worker cleanup must not lose a save
            # accepted from a completed response, before its task gets to run.
            await service.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
            await service.cleanup()
        elif ending == "new_response":
            service._begin_tool_response()
        release.set()
        await asyncio.wait_for(engine.finish_tool_calls(), 1)
        assert invocations == [("save", {"reason": "tomorrow"})]
        (saved,) = engine._gathered_context["tool_results"]
        assert saved["status"] == "completed"
        assert saved["result"] == {"saved": True}
        # Acceptance does not deliver results or cause another inference while
        # the goodbye is still playing. The normal dispatcher retains the group.
        assert not any(
            isinstance(f, (FunctionCallsStartedFrame, FunctionCallResultFrame))
            for f in frames
        )

        await service._run_pending_node_transition_function_calls(
            BotStoppedSpeakingFrame()
        )
        if ending == "playback":
            await asyncio.wait_for(results_delivered.wait(), 1)
            assert invocations == [("save", {"reason": "tomorrow"}), ("end_call", {})]
            started = [f for f in frames if isinstance(f, FunctionCallsStartedFrame)]
            assert len(started) == 1
            assert [c.function_name for c in started[0].function_calls] == [
                "save",
                "end_call",
            ]
            progress = [f for f in frames if isinstance(f, FunctionCallInProgressFrame)]
            assert len(progress) == 2
            assert progress[0].group_id is not None
            assert progress[0].group_id == progress[1].group_id
        else:
            assert invocations == [("save", {"reason": "tomorrow"})]
            assert not results_delivered.is_set()
    finally:
        release.set()
        await service.cleanup()
        await asyncio.wait_for(engine.finish_tool_calls(), 1)
    assert not manager.current_tasks()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "harness", [DograhLLMService, DograhGoogleLLMService], indirect=True
)
@pytest.mark.parametrize("interrupted", [False, True])
async def test_only_fallback_winner_owns_save_before_goodbye(
    harness, simple_workflow, interrupted
):
    engine = PipecatEngine(
        llm=harness.llm,
        workflow=simple_workflow,
        context=gemini.context(),
        call_context_vars={},
        workflow_run_id=123,
    )
    saved = []
    ended = asyncio.Event()

    async def save(params):
        saved.append(params.arguments)
        await params.result_callback({"saved": True})

    async def end(params):
        ended.set()
        await params.result_callback(
            {"ended": True}, properties=FunctionCallResultProperties(run_llm=False)
        )

    harness.llm.register_function("save", engine.active_agent.bind_tool(engine, save))
    harness.llm.register_function(
        "end_call",
        engine.active_agent.bind_tool(engine, end, is_node_transition=True),
        is_node_transition=True,
    )
    streams = []
    for index, service in enumerate(harness.llm._services):
        text = (
            'Goodbye! default_api:save{"reason":"'
            + ("primary" if index == 0 else "backup")
            + '"} default_api:end_call{}'
        )
        steps = [asyncio.Event()] if index == 0 else []
        if isinstance(service, DograhLLMService):
            stream = fallback.Stream(
                *steps, openai.chunk(text), openai.chunk(finish="stop")
            )
            service._client.chat.completions.create = AsyncMock(return_value=stream())
        else:
            stream = fallback.Stream(
                *steps,
                gemini.chunk(Part(text=text)),
                gemini.chunk(finish=FinishReason.STOP),
            )
            service._stream_content = AsyncMock(side_effect=lambda _, s=stream: s())
        streams.append(stream)

    try:
        speech = await fallback.generate(harness, context=engine.context)
        assert "".join(speech).strip() == "Goodbye!"
        assert all(stream.closed.is_set() for stream in streams)
        assert harness.llm.fallback_metrics == {"started": 1, "won": 1}
        if interrupted:
            await fallback.control(harness, InterruptionFrame())
        await asyncio.wait_for(engine.finish_tool_calls(), 1)
        assert saved == [{"reason": "backup"}]
        assert not ended.is_set()
        assert not any(
            isinstance(f, FunctionCallResultFrame) for f in harness.downstream.frames
        )
        await fallback.control(harness, BotStoppedSpeakingFrame())
        if interrupted:
            assert not ended.is_set()
        else:
            await asyncio.wait_for(ended.wait(), 1)
        assert saved == [{"reason": "backup"}]
    finally:
        await asyncio.wait_for(engine.finish_tool_calls(), 1)
