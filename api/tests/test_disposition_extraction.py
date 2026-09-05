"""Tests for workflow-configured terminal call-disposition extraction."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.utils.enums import EndTaskReason

from api.schemas.workflow_configurations import CallDispositionOption
from api.services.workflow.conversation_history import (
    build_conversation_history,
    has_user_turns,
)
from api.services.workflow.disposition_extraction import (
    CALL_DISPOSITION_CONTEXT_KEY,
    DISPOSITION_EXTRACTION_TRACE_NAME,
    DispositionExtractionService,
)
from api.services.workflow.dto import ExtractionVariableDTO
from api.services.workflow.pipecat_engine import PipecatEngine

_DISPOSITIONS = (
    CallDispositionOption(
        code="call_rescheduled",
        description="The caller booked another conversation.",
    ),
    CallDispositionOption(
        code="not_interested",
        description="The caller clearly declined the offer.",
    ),
)


def _service(
    *,
    response: str | None = '{"call_disposition": "call_rescheduled"}',
    messages: list[dict] | None = None,
    options: tuple[CallDispositionOption, ...] = _DISPOSITIONS,
    template_context: dict | None = None,
) -> tuple[DispositionExtractionService, SimpleNamespace]:
    llm = SimpleNamespace(
        run_inference=AsyncMock(return_value=response),
        model_name="disposition-model",
    )
    context = LLMContext(
        messages=messages
        or [
            {"role": "assistant", "content": "When should I call back?"},
            {"role": "user", "content": "Next Tuesday works."},
        ]
    )
    return (
        DispositionExtractionService(
            llm=llm,
            context=context,
            options=options,
            template_context=template_context or {},
        ),
        llm,
    )


@pytest.mark.asyncio
async def test_configured_workflow_extracts_one_supported_disposition():
    service, llm = _service()

    with patch(
        "api.services.workflow.disposition_extraction.ensure_tracing",
        return_value=False,
    ):
        result = await service.extract()

    assert result == "call_rescheduled"
    inference_context = llm.run_inference.await_args.args[0]
    prompt = inference_context.messages[0]["content"]
    assert '"code": "call_rescheduled"' in prompt
    assert "Conversation history:\nassistant: When should I call back?" in prompt
    assert llm.run_inference.await_args.kwargs["system_instruction"]


@pytest.mark.asyncio
async def test_unconfigured_workflow_skips_disposition_extraction():
    service, llm = _service(options=())

    result = await service.extract()

    assert result is None
    llm.run_inference.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_user_speech_skips_disposition_extraction():
    service, llm = _service(
        messages=[{"role": "assistant", "content": "Hello?"}],
    )

    result = await service.extract()

    assert result is None
    llm.run_inference.assert_not_awaited()


@pytest.mark.asyncio
async def test_unconfigured_model_result_keeps_the_fallback():
    service, _llm = _service(
        response='{"call_disposition": "highly_qualified"}',
    )

    with patch(
        "api.services.workflow.disposition_extraction.ensure_tracing",
        return_value=False,
    ):
        result = await service.extract()

    assert result is None


@pytest.mark.asyncio
async def test_configured_code_is_canonicalized_case_insensitively():
    service, _llm = _service(
        response='{"call_disposition": " CALL_RESCHEDULED "}',
    )

    with patch(
        "api.services.workflow.disposition_extraction.ensure_tracing",
        return_value=False,
    ):
        result = await service.extract()

    assert result == "call_rescheduled"


@pytest.mark.asyncio
async def test_non_object_model_result_keeps_the_fallback():
    service, _llm = _service(response='["call_rescheduled"]')

    with patch(
        "api.services.workflow.disposition_extraction.ensure_tracing",
        return_value=False,
    ):
        result = await service.extract()

    assert result is None


@pytest.mark.asyncio
async def test_disposition_descriptions_render_initial_context():
    options = (
        CallDispositionOption(
            code="qualified",
            description="The caller confirmed {{account_name}}.",
        ),
    )
    service, llm = _service(
        response='{"call_disposition": "qualified"}',
        options=options,
        template_context={"account_name": "Acme"},
    )

    with patch(
        "api.services.workflow.disposition_extraction.ensure_tracing",
        return_value=False,
    ):
        assert await service.extract() == "qualified"

    prompt = llm.run_inference.await_args.args[0].messages[0]["content"]
    assert "The caller confirmed Acme." in prompt


@pytest.mark.asyncio
async def test_disposition_extraction_failure_is_best_effort():
    service, llm = _service()
    llm.run_inference.side_effect = RuntimeError("provider unavailable")
    metadata = SimpleNamespace(
        source="provider",
        provider="test",
        error_owner="operator",
    )

    with (
        patch(
            "api.services.workflow.disposition_extraction.ensure_tracing",
            return_value=False,
        ),
        patch(
            "api.services.workflow.disposition_extraction.failure_metadata_for_processor",
            return_value=metadata,
        ),
        patch(
            "api.services.workflow.disposition_extraction.classify_exception",
            return_value="classified failure",
        ) as classify_exception,
        patch(
            "api.services.workflow.disposition_extraction.log_failure"
        ) as log_failure,
    ):
        result = await service.extract(organization_id=2, workflow_run_id=7)

    assert result is None
    classify_exception.assert_called_once_with(
        llm.run_inference.side_effect,
        source="provider",
        provider="test",
        error_owner="operator",
    )
    assert log_failure.call_args.args == ("classified failure",)
    assert log_failure.call_args.kwargs == {
        "organization_id": 2,
        "workflow_run_id": 7,
        "node_name": DISPOSITION_EXTRACTION_TRACE_NAME,
    }


@pytest.mark.asyncio
async def test_trace_is_named_disposition_extraction():
    service, _llm = _service()
    tracer = MagicMock()
    span = tracer.start_as_current_span.return_value.__enter__.return_value
    parent_context = object()

    with (
        patch(
            "api.services.workflow.disposition_extraction.ensure_tracing",
            return_value=True,
        ),
        patch(
            "api.services.workflow.disposition_extraction.trace.get_tracer",
            return_value=tracer,
        ),
        patch(
            "api.services.workflow.disposition_extraction.add_llm_span_attributes"
        ) as add_span_attributes,
    ):
        assert await service.extract(parent_context=parent_context) == (
            "call_rescheduled"
        )

    tracer.start_as_current_span.assert_called_once_with(
        DISPOSITION_EXTRACTION_TRACE_NAME,
        context=parent_context,
    )
    assert add_span_attributes.call_args.args[0] is span
    assert (
        add_span_attributes.call_args.kwargs["operation_name"]
        == DISPOSITION_EXTRACTION_TRACE_NAME
    )


def _engine(**context) -> PipecatEngine:
    engine = PipecatEngine(workflow=None, call_context_vars={})
    engine._gathered_context.update(context)
    return engine


def test_extracted_disposition_replaces_the_fallback_and_is_remapped():
    engine = _engine(call_disposition=EndTaskReason.USER_HANGUP.value)
    engine._disposition_mapping = {"call_rescheduled": "RESCH"}

    engine.refine_call_disposition(
        EndTaskReason.USER_HANGUP.value,
        "call_rescheduled",
    )

    assert engine._gathered_context["call_disposition"] == "call_rescheduled"
    assert engine._gathered_context["mapped_call_disposition"] == "RESCH"
    assert "call_rescheduled" in engine._gathered_context["call_tags"]


def test_business_outcome_is_tagged_alongside_the_mechanism():
    engine = _engine(
        call_disposition=EndTaskReason.USER_HANGUP.value,
        call_tags=[EndTaskReason.USER_HANGUP.value],
    )

    engine.refine_call_disposition(
        EndTaskReason.USER_HANGUP.value,
        "not_interested",
    )

    assert engine._gathered_context["call_tags"] == ["user_hangup", "not_interested"]


def test_explicit_disposition_survives_late_classification():
    engine = _engine(call_disposition="do_not_call")

    engine.refine_call_disposition(
        EndTaskReason.USER_HANGUP.value,
        "call_rescheduled",
    )

    assert engine._gathered_context["call_disposition"] == "do_not_call"


def test_missing_classification_keeps_the_fallback():
    engine = _engine(call_disposition="end_call")

    engine.refine_call_disposition("end_call", None)

    assert engine._gathered_context["call_disposition"] == "end_call"


@pytest.mark.asyncio
async def test_final_variable_extraction_does_not_run_disposition_service():
    engine = _engine()
    engine._await_pending_extractions = AsyncMock()
    engine._perform_variable_extraction_if_needed = AsyncMock(return_value=None)
    engine._disposition_extraction_service = MagicMock()
    engine._disposition_extraction_service.extract = AsyncMock()

    await engine.perform_final_variable_extraction()

    engine._perform_variable_extraction_if_needed.assert_awaited_once()
    engine._disposition_extraction_service.extract.assert_not_awaited()


@pytest.mark.asyncio
async def test_node_extraction_never_asks_for_engine_owned_fields():
    engine = _engine()
    node = MagicMock(
        name="Qualification",
        extraction_enabled=True,
        extraction_prompt="",
        extraction_variables=[
            ExtractionVariableDTO(name="state", type="string", prompt="US state"),
            ExtractionVariableDTO(
                name=CALL_DISPOSITION_CONTEXT_KEY,
                type="string",
                prompt="node-level outcome",
            ),
            ExtractionVariableDTO(
                name="call_status",
                type="string",
                prompt="mechanical outcome",
            ),
        ],
    )
    engine._variable_extraction_manager = MagicMock()
    engine._variable_extraction_manager._perform_extraction = AsyncMock(return_value={})

    await engine._perform_variable_extraction_if_needed(node, run_in_background=False)

    variables = engine._variable_extraction_manager._perform_extraction.await_args.args[
        0
    ]
    assert [variable.name for variable in variables] == ["state"]


def test_conversation_history_detects_user_speech():
    context = LLMContext(
        messages=[
            {"role": "assistant", "content": "Hi, is this Lois?"},
            {"role": "user", "content": "Please call me next Tuesday."},
        ]
    )

    assert has_user_turns(context)
    assert build_conversation_history(context) == (
        "assistant: Hi, is this Lois?\nuser: Please call me next Tuesday."
    )


def test_conversation_history_includes_tool_parameters_and_relevant_responses():
    context = LLMContext(
        messages=[
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "lookup-1",
                        "function": {
                            "name": "lookup_account",
                            "arguments": '{"account_id": 42}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "lookup-1",
                "content": '{"status": "ok", "data": {"tier": "gold"}}',
            },
        ]
    )

    history = build_conversation_history(context)

    assert history == (
        '[Tool Call: lookup_account]\nArguments: {"account_id": 42}\n'
        '[Tool Response: lookup_account]\nResponse: {"tier": "gold"}'
    )


def test_conversation_history_labels_empty_arguments_and_transition_response():
    context = LLMContext(
        messages=[
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "transition-1",
                        "function": {
                            "name": "move_to_end_call",
                            "arguments": "{}",
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "transition-1",
                "content": '{"status": "done"}',
            },
        ]
    )

    assert build_conversation_history(context) == (
        "[Tool Call: move_to_end_call]\nArguments: {}\n"
        "[Tool Response: move_to_end_call]\nResponse: completed"
    )


@pytest.mark.asyncio
async def test_disposition_prompt_includes_tool_parameters():
    service, llm = _service(
        messages=[
            {"role": "user", "content": "Please look up account 42."},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "lookup-1",
                        "function": {
                            "name": "lookup_account",
                            "arguments": '{"account_id": 42}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "lookup-1",
                "content": '{"status": "ok", "data": {"tier": "gold"}}',
            },
        ]
    )

    with patch(
        "api.services.workflow.disposition_extraction.ensure_tracing",
        return_value=False,
    ):
        await service.extract()

    prompt = llm.run_inference.await_args.args[0].messages[0]["content"]
    assert '[Tool Call: lookup_account]\nArguments: {"account_id": 42}' in prompt
