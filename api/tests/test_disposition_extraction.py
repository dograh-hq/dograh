"""Tests for workflow-configured terminal call-disposition extraction."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.utils.enums import EndTaskReason

from api.schemas.workflow_configurations import CallDispositionOption
from api.services.workflow.disposition_extraction import (
    CALL_DISPOSITION_VARIABLE,
    CALL_STATUS_CONTEXT_KEY,
    END_REASON_CONTEXT_KEY,
    build_disposition_variable,
    coerce_disposition,
    prepare_extraction_variables,
)
from api.services.workflow.dto import ExtractionVariableDTO
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_variable_extractor import (
    VariableExtractionManager,
)

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


def _engine(
    *, dispositions=_DISPOSITIONS, has_user_turns=True, **context
) -> PipecatEngine:
    engine = PipecatEngine(
        workflow=None,
        call_context_vars={},
        call_dispositions=dispositions,
    )
    engine._gathered_context.update(context)
    engine._variable_extraction_manager = MagicMock()
    engine._variable_extraction_manager.has_user_turns.return_value = has_user_turns
    engine._variable_extraction_manager._perform_extraction = AsyncMock(
        return_value={CALL_DISPOSITION_VARIABLE: "call_rescheduled"}
    )
    return engine


def test_call_disposition_is_reserved_from_node_extraction():
    variables = [
        ExtractionVariableDTO(name="state", type="string", prompt="US state"),
        ExtractionVariableDTO(
            name=CALL_DISPOSITION_VARIABLE,
            type="string",
            prompt="node-level custom outcome",
        ),
        ExtractionVariableDTO(
            name=CALL_STATUS_CONTEXT_KEY,
            type="string",
            prompt="Why the call ended",
        ),
        ExtractionVariableDTO(
            name=END_REASON_CONTEXT_KEY,
            type="string",
            prompt="Why the call ended",
        ),
    ]

    result = prepare_extraction_variables(variables)

    assert [variable.name for variable in result] == ["state"]


def test_call_disposition_variable_builds_prompt_from_configured_options():
    variable = build_disposition_variable(_DISPOSITIONS)

    assert variable.name == CALL_DISPOSITION_VARIABLE
    assert "Return the code exactly as written" in variable.prompt
    assert '"code": "call_rescheduled"' in variable.prompt
    assert '"description": "The caller booked another conversation."' in variable.prompt
    assert '"code": "not_interested"' in variable.prompt


@pytest.mark.asyncio
async def test_unconfigured_workflow_does_not_run_dedicated_disposition_extraction():
    engine = _engine(dispositions=())

    result = await engine._perform_final_disposition_extraction()

    assert result is None
    engine._variable_extraction_manager._perform_extraction.assert_not_awaited()


@pytest.mark.asyncio
async def test_configured_workflow_runs_one_dedicated_disposition_extraction():
    engine = _engine()

    result = await engine._perform_final_disposition_extraction()

    assert result == {CALL_DISPOSITION_VARIABLE: "call_rescheduled"}
    variables = engine._variable_extraction_manager._perform_extraction.await_args.args[
        0
    ]
    assert [variable.name for variable in variables] == [CALL_DISPOSITION_VARIABLE]
    assert '"code": "call_rescheduled"' in variables[0].prompt


@pytest.mark.asyncio
async def test_no_user_speech_skips_the_configured_disposition_extraction():
    engine = _engine(has_user_turns=False)

    result = await engine._perform_final_disposition_extraction()

    assert result is None
    engine._variable_extraction_manager._perform_extraction.assert_not_awaited()


@pytest.mark.asyncio
async def test_disposition_extraction_failure_keeps_the_technical_fallback():
    engine = _engine()
    engine._variable_extraction_manager._perform_extraction.side_effect = RuntimeError(
        "provider unavailable"
    )

    result = await engine._perform_final_disposition_extraction()

    assert result is None


@pytest.mark.asyncio
async def test_final_extraction_only_runs_the_disposition_prompt_when_requested():
    engine = _engine()
    engine._perform_variable_extraction_if_needed = AsyncMock(return_value=None)
    engine._await_pending_extractions = AsyncMock()

    result = await engine.perform_final_variable_extraction(extract_disposition=True)

    assert result == {CALL_DISPOSITION_VARIABLE: "call_rescheduled"}
    engine._perform_variable_extraction_if_needed.assert_awaited_once()
    engine._variable_extraction_manager._perform_extraction.assert_awaited_once()


@pytest.mark.parametrize(
    "value,expected",
    [
        ("voicemail_detected", "voicemail_detected"),
        ("  Call_Rescheduled  ", "Call_Rescheduled"),
        ("sold", "sold"),
    ],
)
def test_usable_disposition_is_kept(value, expected):
    assert coerce_disposition(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "unknown",
        "UNKNOWN",
        "n/a",
        {"disposition": "voicemail"},
        ["voicemail"],
        True,
        "the caller said they were not interested and hung up before " * 3,
    ],
)
def test_non_answers_are_rejected(value):
    assert coerce_disposition(value) is None


def test_configured_disposition_is_canonicalized_case_insensitively():
    assert coerce_disposition(" Qualified ", ["qualified", "not_interested"]) == (
        "qualified"
    )


def test_unconfigured_disposition_is_rejected():
    assert (
        coerce_disposition("highly_qualified", ["qualified", "not_interested"]) is None
    )


def test_unconfigured_model_result_keeps_the_technical_fallback():
    engine = _engine(call_disposition=EndTaskReason.USER_HANGUP.value)

    engine.refine_call_disposition(
        EndTaskReason.USER_HANGUP.value,
        {CALL_DISPOSITION_VARIABLE: "highly_qualified"},
    )

    assert engine._gathered_context["call_disposition"] == "user_hangup"


def test_final_disposition_replaces_a_mechanical_fallback_and_remaps_it():
    engine = _engine(call_disposition=EndTaskReason.USER_HANGUP.value)
    engine._disposition_mapping = {"call_rescheduled": "RESCH"}

    engine.refine_call_disposition(
        EndTaskReason.USER_HANGUP.value,
        {CALL_DISPOSITION_VARIABLE: "call_rescheduled"},
    )

    assert engine._gathered_context["call_disposition"] == "call_rescheduled"
    assert engine._gathered_context["mapped_call_disposition"] == "RESCH"
    assert "call_rescheduled" in engine._gathered_context["call_tags"]


def test_the_business_outcome_is_tagged_alongside_the_mechanism():
    engine = _engine(
        call_disposition=EndTaskReason.USER_HANGUP.value,
        call_tags=[EndTaskReason.USER_HANGUP.value],
    )

    engine.refine_call_disposition(
        EndTaskReason.USER_HANGUP.value,
        {CALL_DISPOSITION_VARIABLE: "not_interested"},
    )

    assert engine._gathered_context["call_tags"] == ["user_hangup", "not_interested"]


def test_explicit_disposition_survives_the_final_extraction():
    engine = _engine(call_disposition="do_not_call")

    engine.refine_call_disposition(
        EndTaskReason.USER_HANGUP.value,
        {CALL_DISPOSITION_VARIABLE: "call_rescheduled"},
    )

    assert engine._gathered_context["call_disposition"] == "do_not_call"


def test_no_user_speech_keeps_the_technical_fallback():
    engine = _engine(
        has_user_turns=False,
        call_disposition=EndTaskReason.USER_HANGUP.value,
    )

    engine.refine_call_disposition(
        EndTaskReason.USER_HANGUP.value,
        {CALL_DISPOSITION_VARIABLE: "not_interested"},
    )

    assert engine._gathered_context["call_disposition"] == "user_hangup"


def test_missing_or_unusable_final_result_keeps_the_technical_fallback():
    engine = _engine(call_disposition="end_call")

    engine.refine_call_disposition("end_call", None)
    assert engine._gathered_context["call_disposition"] == "end_call"

    engine.refine_call_disposition(
        "end_call",
        {CALL_DISPOSITION_VARIABLE: "unknown"},
    )
    assert engine._gathered_context["call_disposition"] == "end_call"


def test_run_torn_down_before_initialization_is_not_refined():
    engine = _engine(call_disposition=EndTaskReason.USER_HANGUP.value)
    engine._variable_extraction_manager = None

    engine.refine_call_disposition(
        EndTaskReason.USER_HANGUP.value,
        {CALL_DISPOSITION_VARIABLE: "not_interested"},
    )

    assert engine._gathered_context["call_disposition"] == "user_hangup"


@pytest.mark.asyncio
async def test_node_extraction_never_asks_for_the_disposition():
    engine = PipecatEngine(workflow=None, call_context_vars={})
    node = MagicMock(
        name="Qualification",
        extraction_enabled=True,
        extraction_prompt="",
        extraction_variables=[
            ExtractionVariableDTO(name="state", type="string", prompt="US state"),
            ExtractionVariableDTO(
                name=CALL_DISPOSITION_VARIABLE,
                type="string",
                prompt="node-level outcome",
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


class TestHasUserTurns:
    def _manager(self, messages):
        engine = PipecatEngine(workflow=None, call_context_vars={})
        engine.context = LLMContext()
        engine.context.set_messages(messages)
        return VariableExtractionManager(engine)

    def test_caller_speech_is_detected(self):
        assert self._manager(
            [
                {"role": "assistant", "content": "Hi, is this Lois?"},
                {"role": "user", "content": "Please call me next Tuesday."},
            ]
        ).has_user_turns()

    def test_only_agent_speech_is_not_enough(self):
        assert not self._manager(
            [{"role": "assistant", "content": "Hi, is this Lois?"}]
        ).has_user_turns()
