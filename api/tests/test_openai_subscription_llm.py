"""Exercise pinned Responses parsing with synthetic subscription HTTP streams."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from pipecat.frames.frames import EndFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)

from api.services.configuration.openai_subscription_responses import (
    SubscriptionResponsesError,
)
from api.services.pipecat.pipeline_metrics_aggregator import PipelineMetricsAggregator
from api.services.pipecat.realtime.openai_subscription_llm import (
    SubscriptionResponsesLLMService,
)
from api.services.pipecat.worker_runner import (
    run_pipeline_worker,
    wait_for_pipeline_worker_started,
)
from api.services.workflow.pipecat_engine import PipecatEngine
from api.tests.test_openai_subscription_wrapper import make_service


class SyntheticStream(httpx.AsyncByteStream):
    def __init__(self, events, *, block=False):
        self.payload = b"".join(
            b"data: " + json.dumps(event).encode() + b"\n\n" for event in events
        )
        self.block = block
        self.entered = asyncio.Event()
        self.closed = False

    async def __aiter__(self):
        self.entered.set()
        if self.block:
            await asyncio.Event().wait()
        yield self.payload

    async def aclose(self):
        self.closed = True


def completed(output):
    return {
        "type": "response.completed",
        "sequence_number": 5,
        "response": {
            "id": "response_synthetic",
            "object": "response",
            "created_at": 2000000000,
            "model": "gpt-5.6-luna",
            "status": "completed",
            "output": output,
            "usage": {
                "input_tokens": 8,
                "output_tokens": 4,
                "total_tokens": 12,
                "input_tokens_details": {"cached_tokens": 2},
                "output_tokens_details": {"reasoning_tokens": 1},
            },
        },
    }


def text_events(text):
    message = {
        "type": "message",
        "id": "message_synthetic",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }
    return [
        {
            "type": "response.output_text.delta",
            "sequence_number": 1,
            "item_id": message["id"],
            "output_index": 0,
            "content_index": 0,
            "delta": text,
        },
        {
            "type": "response.output_item.done",
            "sequence_number": 2,
            "output_index": 0,
            "item": message,
        },
        completed(None),
    ]


def fake_auth():
    return SimpleNamespace(
        assert_organization=Mock(),
        get_credentials=AsyncMock(
            return_value=SimpleNamespace(
                access_token="synthetic-oauth", account_id="synthetic-account"
            )
        ),
        aclose=AsyncMock(),
    )


def inference_service(auth, *, owned=False):
    return SubscriptionResponsesLLMService(
        auth_service=auth,
        organization_id=1,
        owns_auth_service=owned,
        settings=SubscriptionResponsesLLMService.Settings(
            model="gpt-5.6-luna", system_instruction="Follow the workflow."
        ),
    )


@pytest.mark.asyncio
async def test_inference_uses_subscription_account_and_never_constructs_api_client(
    monkeypatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-forbidden-api-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://forbidden.invalid")
    auth, requests = fake_auth(), []
    stream = SyntheticStream(text_events("Synthetic summary."))

    async def handler(request):
        requests.append(request)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stream
        )

    with patch(
        "pipecat.services.openai.responses.llm.AsyncOpenAI",
        side_effect=AssertionError("API client forbidden"),
    ):
        service = inference_service(auth)
        service._subscription_client._transport = httpx.MockTransport(handler)
        service.bind_account("synthetic-account")
        result = await service.run_inference(
            LLMContext([{"role": "user", "content": "Summarize the synthetic call."}]),
            max_tokens=100,
            system_instruction="Only summarize the supplied call.",
        )
    assert result == "Synthetic summary."
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://chatgpt.com/backend-api/codex/responses"
    assert request.headers["authorization"] == "Bearer synthetic-oauth"
    assert request.headers["chatgpt-account-id"] == "synthetic-account"
    body = json.loads(request.content)
    assert body["model"] == "gpt-5.6-luna"
    assert "Only summarize the supplied call." in body["instructions"]
    assert "max_output_tokens" not in body
    assert body["store"] is False
    assert body["stream"] is True
    auth.get_credentials.assert_awaited_once_with(
        1, expected_account_id="synthetic-account"
    )
    assert service._client is None
    assert service._api_key is None
    assert stream.closed
    assert not service._subscription_client._streams
    await service.aclose()
    auth.aclose.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_inference_failure_and_cancellation_close_stream_without_retry(cancel):
    auth, requests = fake_auth(), []
    stream = SyntheticStream(
        [{"type": "error", "message": "synthetic-sensitive-detail"}], block=cancel
    )

    async def handler(request):
        requests.append(request)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stream
        )

    service = inference_service(auth, owned=True)
    service._subscription_client._transport = httpx.MockTransport(handler)
    pending = asyncio.create_task(
        service.run_inference(LLMContext([{"role": "user", "content": "Test"}]))
    )
    await asyncio.wait_for(stream.entered.wait(), 2)
    if cancel:
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    else:
        with pytest.raises(SubscriptionResponsesError) as raised:
            await pending
        assert "synthetic-sensitive-detail" not in str(raised.value)
    assert len(requests) == 1
    assert stream.closed
    assert not service._subscription_client._streams
    await service.aclose()
    auth.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_owned_client_cleanup_survives_caller_cancellation_and_runs_once():
    auth = fake_auth()
    service = inference_service(auth, owned=True)
    entered, release = asyncio.Event(), asyncio.Event()

    async def close():
        entered.set()
        await release.wait()

    service._subscription_client.aclose = AsyncMock(side_effect=close)
    first = asyncio.create_task(service.aclose())
    await asyncio.wait_for(entered.wait(), 2)
    second = asyncio.create_task(service.aclose())
    first.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    await service.aclose()
    service._subscription_client.aclose.assert_awaited_once()
    auth.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_owned_auth_cleanup_runs_even_if_client_cleanup_fails():
    auth = fake_auth()
    service = inference_service(auth, owned=True)
    service._subscription_client.aclose = AsyncMock(
        side_effect=RuntimeError("synthetic close failure")
    )
    with pytest.raises(RuntimeError, match="synthetic close failure"):
        await service.aclose()
    auth.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_real_sse_parser_worker_and_dograh_transition_record_tool_once(
    three_node_workflow_no_variable_extraction,
):
    service = make_service(ready=False, mocked_frames=False)
    service._auth_service.get_credentials = fake_auth().get_credentials
    context = LLMContext()
    service._context = context
    engine = PipecatEngine(
        llm=service,
        context=context,
        workflow=three_node_workflow_no_variable_extraction,
        call_context_vars={},
        is_realtime=True,
    )
    await engine.set_node("start")
    first_tool = context.tools.standard_tools[0].name
    requests, streams = [], []

    async def handler(request):
        requests.append(request)
        if len(requests) == 1:
            tool = {
                "type": "function_call",
                "id": "item_synthetic",
                "call_id": "workflow_transition_sse",
                "name": first_tool,
                "arguments": "{}",
                "status": "completed",
            }
            events = [
                {
                    "type": "response.output_item.added",
                    "sequence_number": 1,
                    "output_index": 0,
                    "item": {**tool, "arguments": "", "status": "in_progress"},
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "sequence_number": 2,
                    "output_index": 0,
                    "item_id": tool["id"],
                    "delta": "{}",
                },
                {
                    "type": "response.function_call_arguments.done",
                    "sequence_number": 3,
                    "output_index": 0,
                    "item_id": tool["id"],
                    "name": first_tool,
                    "arguments": "{}",
                },
                {
                    "type": "response.output_item.done",
                    "sequence_number": 4,
                    "output_index": 0,
                    "item": tool,
                },
                completed([tool]),
            ]
        else:
            events = text_events("The synthetic workflow moved to collect information.")
        stream = SyntheticStream(events)
        streams.append(stream)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stream
        )

    service._backend_llm._subscription_client._transport = httpx.MockTransport(handler)
    aggregators = LLMContextAggregatorPair(context)
    metrics = PipelineMetricsAggregator()
    worker = PipelineWorker(
        Pipeline([aggregators.user(), service, metrics, aggregators.assistant()]),
        params=PipelineParams(),
        enable_rtvi=False,
    )
    engine.set_task(worker)
    run = asyncio.create_task(run_pipeline_worker(worker))
    try:
        await wait_for_pipeline_worker_started(worker, timeout=3, run_task=run)
        await service._handle_context(context)
        await asyncio.wait_for(
            asyncio.gather(*list(service._delegation_tasks.values())), timeout=5
        )
        assert engine._current_node.id == "agent"
        assert len(requests) == 2
        first_body, next_body = [json.loads(request.content) for request in requests]
        assert first_tool in {tool["name"] for tool in first_body["tools"]}
        assert first_tool not in {tool["name"] for tool in next_body["tools"]}
        assert "Agent Node System Prompt" in next_body["instructions"]
        receipts = [
            item
            for item in next_body["input"]
            if item.get("type") == "function_call_output"
        ]
        assert len(receipts) == 1
        assert receipts[0]["call_id"] == "workflow_transition_sse"
        assert "done" in receipts[0]["output"]
        assert (
            sum(
                message.get("tool_call_id") == "workflow_transition_sse"
                for message in context.messages
            )
            == 1
        )
        wire = [call.args[0] for call in service._transport.send_event.await_args_list]
        assert sum("synthetic workflow moved" in str(event) for event in wire) == 1
        assert not service._terminal
        usage = metrics.get_all_usage_metrics_serialized()
        assert usage["llm"] == {}
        observed = list(usage["subscription_reasoning"].values())
        assert len(observed) == 1
        assert observed[0]["tokens"]["total_tokens"] == 24
        assert observed[0]["cost_usd"] is None
        assert all(stream.closed for stream in streams)
        for call in service._auth_service.get_credentials.await_args_list:
            assert call.args == (1,)
            assert call.kwargs == {"expected_account_id": "synthetic-account"}
    finally:
        await worker.queue_frame(EndFrame())
        await asyncio.wait_for(run, timeout=5)
    assert service._backend_worker.has_finished()
    assert service._backend_llm._subscription_client._closed
    assert service._lease is None
    service._auth_service.aclose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_real_worker_reasoning_stream_failure_or_disconnect_cleans_owned_resources(
    cancel,
):
    service = make_service(ready=False, mocked_frames=False)
    service.FAILURE_SPEECH_START_TIMEOUT = 0.01
    service._auth_service.get_credentials = fake_auth().get_credentials
    context = LLMContext([{"role": "user", "content": "Synthetic request"}])
    service._context = context
    requests = []
    stream = SyntheticStream(
        [{"type": "error", "message": "synthetic-sensitive-provider-detail"}],
        block=cancel,
    )

    async def handler(request):
        requests.append(request)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=stream
        )

    service._backend_llm._subscription_client._transport = httpx.MockTransport(handler)
    aggregators = LLMContextAggregatorPair(context)
    worker = PipelineWorker(
        Pipeline([aggregators.user(), service, aggregators.assistant()]),
        params=PipelineParams(),
        enable_rtvi=False,
    )
    run = asyncio.create_task(run_pipeline_worker(worker))
    try:
        await wait_for_pipeline_worker_started(worker, timeout=3, run_task=run)
        await service._handle_context(context)
        pending = list(service._delegation_tasks.values())
        await asyncio.wait_for(stream.entered.wait(), timeout=2)
        if cancel:
            await asyncio.wait_for(service._disconnect(), timeout=3)
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True), timeout=5
        )
        assert len(requests) == 1
        assert stream.closed
        assert service._terminal
        assert not service._backend_llm._subscription_client._streams
        assert service._lease is None
        service._auth_service.aclose.assert_awaited_once()
        assert not any(message.get("role") == "tool" for message in context.messages)
        wire = str(service._transport.send_event.await_args_list)
        assert "synthetic-sensitive-provider-detail" not in wire
        if not cancel:
            assert "I cannot confirm that any action succeeded" in wire
    finally:
        if not worker.has_finished():
            await worker.queue_frame(EndFrame())
        await asyncio.wait_for(run, timeout=5)
    assert service._backend_worker.has_finished()
