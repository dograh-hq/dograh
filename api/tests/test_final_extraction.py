import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from pipecat.frames.frames import CancelFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.utils.enums import EndTaskReason

from api.schemas.workflow_configurations import CallDispositionOption
from api.services.workflow import final_extraction as service
from api.services.workflow.conversation_history import (
    build_conversation_history,
    normalize_conversation,
)
from api.services.workflow.dto import ExtractionVariableDTO
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_variable_extractor import (
    VariableExtractionManager,
)
from api.tasks import workflow_completion


def node(node_id="final", variable="name"):
    return SimpleNamespace(
        id=node_id,
        name=node_id,
        extraction_enabled=True,
        extraction_variables=[
            ExtractionVariableDTO(
                name=variable, type="string", prompt="Extract {{ customer }}"
            )
        ],
        extraction_prompt="For {{ customer }}",
    )


def engine(monkeypatch):
    llm = SimpleNamespace(run_inference=AsyncMock())
    instance = PipecatEngine(
        llm=llm,
        workflow=None,
        call_context_vars={"customer": "Ada"},
        context=LLMContext(messages=[{"role": "user", "content": "My name is Ada"}]),
        task=SimpleNamespace(queue_frame=AsyncMock()),
        workflow_run_id=1,
        defer_final_extraction=True,
    )
    instance.active_agent.current_node = node()
    instance.active_agent.workflow_id = 9
    instance.active_agent.definition_id = 19
    instance.active_agent.model_configuration_snapshot = {"visit": "child"}
    monkeypatch.setattr(instance, "_get_otel_context", lambda: None)
    instance._variable_extraction_manager = VariableExtractionManager(instance)
    return instance, llm


@pytest.mark.asyncio
async def test_terminal_frame_does_not_wait_for_extraction(monkeypatch):
    instance, llm = engine(monkeypatch)
    llm.run_inference.side_effect = lambda *_args, **_kwargs: asyncio.Event().wait()
    await asyncio.wait_for(instance.end_call_with_reason("end_call", True), timeout=0.2)
    llm.run_inference.assert_not_awaited()
    assert isinstance(instance.call_worker.queue_frame.call_args.args[0], CancelFrame)
    assert instance.final_extraction_metadata()["status"] == "pending"
    assert "final_extraction" not in await instance.get_gathered_context()
    request = instance.final_extraction_logs()["final_extraction"]["requests"][0]
    assert request["model_configuration_snapshot"] == {"visit": "child"}
    assert request["variables"][0]["prompt"] == "Extract Ada"
    assert request["prompt"] == "For Ada"


@pytest.mark.asyncio
async def test_pending_transition_retains_its_original_conversation(monkeypatch):
    instance, llm = engine(monkeypatch)
    started = asyncio.Event()

    async def slow(*_args, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    llm.run_inference.side_effect = slow
    await instance._perform_variable_extraction_if_needed(node("earlier"))
    await asyncio.wait_for(started.wait(), timeout=1)
    instance.context.add_message({"role": "user", "content": "A later correction"})
    await asyncio.wait_for(instance.end_call_with_reason("end_call", True), timeout=1)
    requests = instance.final_extraction_logs()["final_extraction"]["requests"]
    assert [request["node_id"] for request in requests] == ["earlier", "final"]
    assert "later correction" not in requests[0]["conversation_history"]
    assert not instance._pending_extraction_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("same_workflow", [True, False])
async def test_pending_current_node_is_extracted_once(monkeypatch, same_workflow):
    instance, llm = engine(monkeypatch)
    started = asyncio.Event()

    async def slow(*_args, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    llm.run_inference.side_effect = slow
    await instance._perform_variable_extraction_if_needed(
        instance.active_agent.current_node
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    instance.context.add_message({"role": "user", "content": "A later correction"})
    if not same_workflow:
        # Node IDs can repeat across transferred workflows.
        instance.active_agent.workflow_id = 10
        instance.active_agent.definition_id = 20
    await asyncio.wait_for(instance.end_call_with_reason("end_call", True), timeout=1)
    logs = instance.final_extraction_logs()
    requests = logs["final_extraction"]["requests"]
    assert len(requests) == (1 if same_workflow else 2)
    assert "conversation_history" not in requests[-1]
    assert logs["conversation"][-1]["content"] == "A later correction"
    assert not instance._pending_extraction_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["nodes_visited", "agent_visits", "agent_transfers"])
async def test_extraction_cannot_replace_engine_history(monkeypatch, key):
    instance, llm = engine(monkeypatch)
    history = [{"node_id": "start"}]
    instance._gathered_context[key] = deepcopy(history)
    current = node(variable=key)
    current.extraction_variables.append(
        ExtractionVariableDTO(name="name", type="string")
    )
    instance.active_agent.current_node = current
    llm.run_inference.return_value = json.dumps({key: "invalid", "name": "Ada"})
    await instance._perform_variable_extraction_if_needed(
        current, run_in_background=False
    )
    assert instance._gathered_context[key] == history
    assert instance._gathered_context["name"] == "Ada"
    await instance.end_call_with_reason("end_call", True)
    request = instance.final_extraction_logs()["final_extraction"]["requests"][0]
    assert [variable["name"] for variable in request["variables"]] == ["name"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    [EndTaskReason.PIPELINE_ERROR.value, EndTaskReason.VOICEMAIL_DETECTED.value],
)
async def test_ineligible_endings_skip_extraction(monkeypatch, reason):
    instance, llm = engine(monkeypatch)
    await instance.end_call_with_reason(reason, True)
    assert instance.final_extraction_metadata()["status"] == "skipped"
    assert "final_extraction" not in await instance.get_gathered_context()
    assert instance.final_extraction_logs()["final_extraction"]["requests"] == []
    llm.run_inference.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_disposition_equal_to_status_is_preserved(monkeypatch):
    instance, _ = engine(monkeypatch)
    instance._call_dispositions = (
        CallDispositionOption(code="qualified", description="Qualified"),
    )
    instance.set_call_disposition("end_call")
    await instance.end_call_with_reason("end_call", True)
    assert (
        instance.final_extraction_logs()["final_extraction"]["disposition"]["options"]
        == []
    )


def test_normalized_conversation_round_trip():
    context = LLMContext(
        messages=[
            {"role": "system", "content": "Private system prompt"},
            {"role": "user", "content": [{"type": "text", "text": "Find Ada"}]},
            {
                "role": "assistant",
                "content": "Looking",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {"name": "lookup", "arguments": '{"name":"Ada"}'},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": '{"data":{"found":true},"status":200}',
            },
        ]
    )
    messages = json.loads(json.dumps(normalize_conversation(context)))
    assert build_conversation_history(
        LLMContext(messages=messages)
    ) == build_conversation_history(context)
    context.messages[2]["tool_calls"][0]["function"]["name"] = "changed"
    assert messages[1]["tool_calls"][0]["function"]["name"] == "lookup"


@pytest.fixture
def worker(monkeypatch):
    request = {
        "workflow_id": 9,
        "definition_id": 19,
        "node_id": "last",
        "variables": [{"name": "name", "type": "string", "prompt": "Name"}],
        "prompt": "",
        "model_configuration_snapshot": {"visit": "child"},
    }
    run = SimpleNamespace(
        definition_id=10,
        workflow_id=8,
        initial_context={"mps_correlation_id": "existing"},
        extra={
            "workflow_completion": {
                "status": "pending",
                "final_extraction": {"status": "pending"},
            }
        },
        gathered_context={
            "call_disposition": "end_call",
        },
        logs={
            "conversation": [{"role": "user", "content": "My name is Ada"}],
            "final_extraction": {
                "version": 1,
                "requests": [request],
                "disposition": {
                    **request,
                    "options": [],
                    "fallback": "end_call",
                    "mapping": {"qualified": "QUAL"},
                },
            },
        },
    )
    monkeypatch.setattr(
        service.db_client,
        "get_workflow_run_with_context",
        AsyncMock(return_value=(run, 7)),
    )
    monkeypatch.setattr(
        service.db_client, "get_workflow_definition", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        service,
        "hydrate_model_configuration_snapshot",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        service, "get_effective_ai_model_configuration_for_run", AsyncMock()
    )
    llm = SimpleNamespace(
        run_inference=AsyncMock(return_value='{"name":"Ada"}'), cleanup=AsyncMock()
    )
    monkeypatch.setattr(service, "create_llm_service", Mock(return_value=llm))
    monkeypatch.setattr(service, "RETRY_DELAYS", (0, 0))

    async def save(*_args, **kwargs):
        run.extra["workflow_completion"]["final_extraction"]["status"] = kwargs[
            "status"
        ]
        return True

    saved = AsyncMock(side_effect=save)
    monkeypatch.setattr(service.db_client, "finish_final_extraction", saved)
    return run, llm, saved


@pytest.mark.asyncio
@pytest.mark.parametrize("variable", ["name", "final_extraction", "raw"])
async def test_worker_uses_child_model_and_is_idempotent(worker, variable):
    run, llm, saved = worker
    run.logs["final_extraction"]["requests"][0]["variables"][0]["name"] = variable
    llm.run_inference.return_value = json.dumps({variable: "Ada"})
    await service.run_final_extraction(1, 7, "lease")
    assert saved.call_args.kwargs["variables"] == {variable: "Ada"}
    assert saved.call_args.kwargs["status"] == "done"
    service.db_client.get_workflow_definition.assert_awaited_once_with(9, 19, 7)
    service.hydrate_model_configuration_snapshot.assert_awaited_once_with(
        7, {"visit": "child"}
    )
    service.get_effective_ai_model_configuration_for_run.assert_not_awaited()
    assert service.create_llm_service.call_args.kwargs["correlation_id"] == "existing"
    llm.cleanup.assert_awaited_once()
    await service.run_final_extraction(1, 7, "lease")
    assert llm.run_inference.await_count == 1


@pytest.mark.asyncio
async def test_user_variable_cannot_control_extraction_state(monkeypatch, worker):
    instance, _ = engine(monkeypatch)
    instance.active_agent.current_node = node(variable="final_extraction")
    instance._gathered_context["final_extraction"] = "user value"
    await instance.end_call_with_reason("end_call", True)
    assert (await instance.get_gathered_context())["final_extraction"] == "user value"
    request = instance.final_extraction_logs()["final_extraction"]["requests"][0]
    assert request["variables"][0]["name"] == "final_extraction"

    run, llm, saved = worker
    run.gathered_context["final_extraction"] = {"status": "done"}
    await service.run_final_extraction(1, 7, "lease")
    llm.run_inference.assert_awaited_once()
    assert saved.call_args.kwargs["status"] == "done"


@pytest.mark.asyncio
async def test_transient_failure_is_retried(worker):
    _, llm, saved = worker
    llm.run_inference.side_effect = [HTTPException(429), '{"name":"Ada"}']
    await service.run_final_extraction(1, 7, "lease")
    assert llm.run_inference.await_count == 2
    assert saved.call_args.kwargs["status"] == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response", [None, "not json", "[]", "{}", '{"unrequested": "Ada"}']
)
async def test_invalid_response_exhausts_retries(worker, response):
    _, llm, saved = worker
    llm.run_inference.return_value = response
    await service.run_final_extraction(1, 7, "lease")
    assert llm.run_inference.await_count == 3
    assert saved.call_args.kwargs["status"] == "failed"
    assert saved.call_args.kwargs["error_class"] == "ExtractionResponseError"


@pytest.mark.asyncio
async def test_raw_variable_does_not_accept_parser_fallback(worker):
    run, llm, saved = worker
    run.logs["final_extraction"]["requests"][0]["variables"][0]["name"] = "raw"
    llm.run_inference.side_effect = ["not JSON", '```json\n{"raw": "Ada"}\n```']
    await service.run_final_extraction(1, 7, "lease")
    assert llm.run_inference.await_count == 2
    assert saved.call_args.kwargs["variables"] == {"raw": "Ada"}
    assert saved.call_args.kwargs["status"] == "done"


@pytest.mark.asyncio
async def test_requested_null_value_is_a_valid_extraction(worker):
    _, llm, saved = worker
    llm.run_inference.return_value = '{"name": null}'
    await service.run_final_extraction(1, 7, "lease")
    llm.run_inference.assert_awaited_once()
    assert saved.call_args.kwargs["variables"] == {"name": None}
    assert saved.call_args.kwargs["status"] == "done"


@pytest.mark.asyncio
async def test_permanent_failure_is_not_retried(worker):
    _, llm, saved = worker
    llm.run_inference.side_effect = HTTPException(401)
    await service.run_final_extraction(1, 7, "lease")
    assert llm.run_inference.await_count == 1
    assert saved.call_args.kwargs["status"] == "failed"


@pytest.mark.asyncio
async def test_budget_exhaustion_closes_client_and_records_failure(worker, monkeypatch):
    _, llm, saved = worker

    async def slow(*_args, **_kwargs):
        await asyncio.Event().wait()

    llm.run_inference.side_effect = slow
    monkeypatch.setattr(service, "FINAL_EXTRACTION_BUDGET_SECONDS", 0.02)
    await asyncio.wait_for(service.run_final_extraction(1, 7, "lease"), 1)
    assert saved.call_args.kwargs["error_class"] == "TimeoutError"
    llm.cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_ordered_requests_and_disposition_mapping(worker):
    run, llm, saved = worker
    payload = run.logs["final_extraction"]
    payload["requests"].insert(0, deepcopy(payload["requests"][0]))
    payload["disposition"]["options"] = [
        {"code": "qualified", "description": "Qualified"}
    ]
    llm.run_inference.side_effect = [
        '{"name":"Old"}',
        '{"name":"Ada"}',
        '{"call_disposition":"qualified"}',
    ]
    await service.run_final_extraction(1, 7, "lease")
    assert saved.call_args.kwargs["variables"] == {"name": "Ada"}
    assert saved.call_args.kwargs["disposition"] == "qualified"
    assert saved.call_args.kwargs["mapped_disposition"] == "QUAL"


@pytest.mark.asyncio
async def test_completion_continues_after_extraction_failure(worker, monkeypatch):
    run, llm, saved = worker
    llm.run_inference.side_effect = HTTPException(401)
    monkeypatch.setattr(
        service.db_client,
        "claim_workflow_completion",
        AsyncMock(return_value={"steps": []}),
    )
    monkeypatch.setattr(
        service.db_client,
        "checkpoint_workflow_completion",
        AsyncMock(return_value=True),
    )
    order = []

    async def step(label):
        assert saved.await_count == 1
        assert (
            run.extra["workflow_completion"]["final_extraction"]["status"] == "failed"
        )
        order.append(label)

    monkeypatch.setattr(
        workflow_completion,
        "run_integrations_post_workflow_run",
        lambda *_: step("integrations"),
    )
    monkeypatch.setattr(
        workflow_completion,
        "report_completed_workflow_run_platform_usage",
        lambda *_: step("billing"),
    )
    monkeypatch.setattr(
        workflow_completion, "sync_external_pbx_call_record", lambda *_: step("pbx")
    )
    await workflow_completion.process_workflow_completion(None, 1)
    assert order == ["integrations", "billing", "pbx"]


@pytest.mark.asyncio
async def test_recovered_job_skips_finished_extraction_and_integrations(
    worker, monkeypatch
):
    run, llm, _ = worker
    run.extra["workflow_completion"]["final_extraction"]["status"] = "done"
    monkeypatch.setattr(
        service.db_client,
        "claim_workflow_completion",
        AsyncMock(return_value={"steps": ["integrations"]}),
    )
    monkeypatch.setattr(
        service.db_client,
        "checkpoint_workflow_completion",
        AsyncMock(return_value=True),
    )
    integrations, billing, pbx = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(
        workflow_completion, "run_integrations_post_workflow_run", integrations
    )
    monkeypatch.setattr(
        workflow_completion, "report_completed_workflow_run_platform_usage", billing
    )
    monkeypatch.setattr(workflow_completion, "sync_external_pbx_call_record", pbx)
    await workflow_completion.process_workflow_completion(None, 1)
    llm.run_inference.assert_not_awaited()
    integrations.assert_not_awaited()
    billing.assert_awaited_once()
    pbx.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_worker_leaves_extraction_pending(worker):
    run, llm, saved = worker
    llm.run_inference.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await service.run_final_extraction(1, 7, "lease")
    saved.assert_not_awaited()
    assert run.extra["workflow_completion"]["final_extraction"]["status"] == "pending"
    llm.cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_standalone_client_can_be_created_and_closed_off_pipeline():
    from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
    from api.tests.integrations._run_pipeline_helpers import USER_CONFIGURATION

    config = EffectiveAIModelConfiguration.model_validate(USER_CONFIGURATION)
    # Client construction and cleanup must work without a running pipeline;
    # no inference request is made with the dummy provider credentials.
    llm = await service._create_llm(config, None)
    assert not llm._client.is_closed()
    await service._close_llm(llm)
    assert llm._client.is_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_fallback_cleanup_closes_every_owned_sdk_client(
    monkeypatch, cleanup_fails
):
    from pipecat.services.openai.llm import OpenAILLMService

    from api.schemas.llm_fallback import ErrorCondition
    from api.services.pipecat.fallback_llm import FallbackLLMProcessor, FallbackRoute

    primary = OpenAILLMService(api_key="test", enable_direct_mode=True)
    fallback = OpenAILLMService(api_key="test", enable_direct_mode=True)
    llm = FallbackLLMProcessor(
        primary, routes=[FallbackRoute(condition=ErrorCondition(), service=fallback)]
    )
    if cleanup_fails:
        monkeypatch.setattr(
            llm, "cleanup", AsyncMock(side_effect=RuntimeError("cleanup failed"))
        )
    await service._close_llm(llm)
    assert primary._client.is_closed()
    assert fallback._client.is_closed()
