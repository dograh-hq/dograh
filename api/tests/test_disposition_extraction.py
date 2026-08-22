"""The extracted outcome replaces the placeholder disposition, or nothing does.

Reaching any end node used to stamp ``user_qualified``. Across the runs on the
dev database that was wrong 87 times out of 87 -- 32 of them voicemail, 21
wrong numbers, 21 not-interested -- while the correct answer already sat in
``extracted_variables`` because the workflow author had written a variable that
asked for it. These cover reading that answer back, and every case where the
recorded value must survive instead.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.utils.enums import EndTaskReason

from api.services.workflow.disposition_extraction import (
    CALL_DISPOSITION_VARIABLE,
    build_disposition_variable,
    coerce_disposition,
    inject_disposition_variable,
)
from api.services.workflow.dto import ExtractionVariableDTO
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_variable_extractor import (
    VariableExtractionManager,
)


def _engine(*, extracted=None, has_user_turns=True, **context) -> PipecatEngine:
    engine = PipecatEngine(workflow=None, call_context_vars={})
    engine._gathered_context.update(context)
    if extracted is not None:
        engine._gathered_context["extracted_variables"] = extracted
    engine._variable_extraction_manager = MagicMock()
    engine._variable_extraction_manager.has_user_turns.return_value = has_user_turns
    return engine


# --------------------------------------------------------------------------
# The variable that carries the outcome
# --------------------------------------------------------------------------


def test_the_disposition_variable_is_added_to_the_authors_own():
    author = [ExtractionVariableDTO(name="state", type="string", prompt="US state")]

    result = inject_disposition_variable(author)

    assert [v.name for v in result] == ["state", CALL_DISPOSITION_VARIABLE]


def test_an_author_declared_disposition_variable_is_left_alone():
    # Editing the hint is how an author picks their own outcome vocabulary, so
    # a declared variable must keep its prompt rather than be replaced.
    author = [
        ExtractionVariableDTO(
            name=CALL_DISPOSITION_VARIABLE,
            type="string",
            prompt="one of: sold, pitched, no_pitch",
        )
    ]

    result = inject_disposition_variable(author)

    assert len(result) == 1
    assert result[0].prompt == "one of: sold, pitched, no_pitch"


def test_the_default_variable_names_outcomes_and_offers_a_way_out():
    prompt = build_disposition_variable().prompt

    assert "voicemail" in prompt
    assert "not_interested" in prompt
    # Without an explicit escape the model picks the closest label rather than
    # declining; the author's prompt on run 1055 forced "other" 10 times.
    assert "unknown" in prompt


# --------------------------------------------------------------------------
# What counts as an answer
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("voicemail", "voicemail"),
        ("  Not_Interested  ", "Not_Interested"),
        ("sold", "sold"),  # author vocabulary, not Dograh's
    ],
)
def test_a_usable_disposition_is_kept(value, expected):
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
def test_a_non_answer_is_rejected(value):
    assert coerce_disposition(value) is None


# --------------------------------------------------------------------------
# When refinement applies
# --------------------------------------------------------------------------


def test_an_end_node_placeholder_is_replaced_by_the_extracted_outcome():
    engine = _engine(
        call_disposition=EndTaskReason.USER_QUALIFIED.value,
        extracted={CALL_DISPOSITION_VARIABLE: "voicemail"},
    )

    engine.refine_call_disposition(EndTaskReason.USER_QUALIFIED.value)

    assert engine._gathered_context["call_disposition"] == "voicemail"


def test_refining_remaps_the_disposition():
    # mapped_call_disposition is what reporting, the run filter and the PBX
    # write-back read. Leaving it on the old value is the desync the owned-keys
    # guard exists to prevent.
    engine = _engine(
        call_disposition=EndTaskReason.USER_QUALIFIED.value,
        extracted={CALL_DISPOSITION_VARIABLE: "voicemail"},
    )
    engine._disposition_mapping = {"voicemail": "AA"}

    engine.refine_call_disposition(EndTaskReason.USER_QUALIFIED.value)

    assert engine._gathered_context["mapped_call_disposition"] == "AA"


def test_a_hangup_after_a_stated_reason_records_that_reason():
    engine = _engine(
        call_disposition=EndTaskReason.USER_HANGUP.value,
        extracted={CALL_DISPOSITION_VARIABLE: "not_interested"},
    )

    engine.refine_call_disposition(EndTaskReason.USER_HANGUP.value)

    assert engine._gathered_context["call_disposition"] == "not_interested"


def test_the_outcome_is_tagged_alongside_the_mechanism():
    # The mechanism is still worth having -- "how often do people hang up on
    # us" is a real question -- it just is not the lead status.
    engine = _engine(
        call_disposition=EndTaskReason.USER_HANGUP.value,
        call_tags=[EndTaskReason.USER_HANGUP.value],
        extracted={CALL_DISPOSITION_VARIABLE: "not_interested"},
    )

    engine.refine_call_disposition(EndTaskReason.USER_HANGUP.value)

    assert engine._gathered_context["call_tags"] == ["user_hangup", "not_interested"]


# --------------------------------------------------------------------------
# When it must not
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    [
        EndTaskReason.VOICEMAIL_DETECTED.value,
        EndTaskReason.CALL_TRANSFERRED.value,
        EndTaskReason.USER_IDLE_MAX_DURATION_EXCEEDED.value,
        EndTaskReason.CALL_DURATION_EXCEEDED.value,
        EndTaskReason.PIPELINE_ERROR.value,
    ],
)
def test_a_disposition_decided_without_an_llm_is_never_second_guessed(reason):
    # A detector fired, or Dograh performed an action. Those are facts; the
    # extraction does not get a vote on them.
    engine = _engine(
        call_disposition=reason,
        extracted={CALL_DISPOSITION_VARIABLE: "not_interested"},
    )

    engine.refine_call_disposition(reason)

    assert engine._gathered_context["call_disposition"] == reason


def test_an_outcome_recorded_during_the_call_survives_teardown():
    # An end-call tool or a DNC request records the outcome while the call is
    # live, so the disposition is no longer the mechanism and is not a
    # placeholder. Compliance depends on this one.
    engine = _engine(
        call_disposition="do_not_call",
        extracted={CALL_DISPOSITION_VARIABLE: "not_interested"},
    )

    engine.refine_call_disposition(EndTaskReason.USER_HANGUP.value)

    assert engine._gathered_context["call_disposition"] == "do_not_call"


def test_a_call_with_no_user_speech_is_not_refined():
    # 83 of 234 hangups had no user transcription at all -- the caller dropped
    # during the greeting. There is nothing to read an outcome from.
    engine = _engine(
        call_disposition=EndTaskReason.USER_HANGUP.value,
        extracted={CALL_DISPOSITION_VARIABLE: "not_interested"},
        has_user_turns=False,
    )

    engine.refine_call_disposition(EndTaskReason.USER_HANGUP.value)

    assert engine._gathered_context["call_disposition"] == "user_hangup"


def test_a_killed_extraction_leaves_the_recorded_disposition():
    # The abrupt-hangup path runs its extraction on a dead socket and has been
    # measured at 21.3s; the pipeline does not wait that long. The floor is the
    # whole point.
    engine = _engine(call_disposition=EndTaskReason.USER_HANGUP.value)

    engine.refine_call_disposition(EndTaskReason.USER_HANGUP.value)

    assert engine._gathered_context["call_disposition"] == "user_hangup"


def test_a_run_torn_down_before_initialization_is_not_refined():
    # `_variable_extraction_manager` is built during `initialize`. A run that
    # fails before that had no conversation either, and teardown must not raise.
    engine = _engine(
        call_disposition=EndTaskReason.USER_HANGUP.value,
        extracted={CALL_DISPOSITION_VARIABLE: "not_interested"},
    )
    engine._variable_extraction_manager = None

    engine.refine_call_disposition(EndTaskReason.USER_HANGUP.value)

    assert engine._gathered_context["call_disposition"] == "user_hangup"


def test_an_unusable_extracted_value_leaves_the_recorded_disposition():
    engine = _engine(
        call_disposition=EndTaskReason.USER_QUALIFIED.value,
        extracted={CALL_DISPOSITION_VARIABLE: "unknown"},
    )

    engine.refine_call_disposition(EndTaskReason.USER_QUALIFIED.value)

    assert engine._gathered_context["call_disposition"] == "user_qualified"


# --------------------------------------------------------------------------
# Reading user speech off the real context
# --------------------------------------------------------------------------


class TestHasUserTurns:
    """The speech gate reads the same context the extraction summarises."""

    def _manager(self, messages):
        engine = PipecatEngine(workflow=None, call_context_vars={})
        engine.context = LLMContext()
        engine.context.set_messages(messages)
        return VariableExtractionManager(engine)

    def test_a_caller_who_spoke_is_detected(self):
        manager = self._manager(
            [
                {"role": "assistant", "content": "Hi, is this Lois?"},
                {"role": "user", "content": "Please leave a message after the tone."},
            ]
        )

        assert manager.has_user_turns() is True

    def test_a_call_with_only_our_own_speech_is_not(self):
        # The caller dropped during the greeting: 83 of 234 hangups look like
        # this, and there is nothing in here to derive an outcome from.
        manager = self._manager(
            [
                {"role": "system", "content": "You are Sarah..."},
                {"role": "assistant", "content": "Hi, is this Lois?"},
            ]
        )

        assert manager.has_user_turns() is False

    def test_an_empty_user_turn_does_not_count(self):
        manager = self._manager(
            [
                {"role": "assistant", "content": "Hi, is this Lois?"},
                {"role": "user", "content": ""},
            ]
        )

        assert manager.has_user_turns() is False


# --------------------------------------------------------------------------
# Only the terminal extraction asks
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_disposition_is_only_asked_for_at_the_end():
    """Mid-call transitions would be asking why a live call ended."""
    engine = PipecatEngine(workflow=None, call_context_vars={})
    node = MagicMock()
    node.name = "Qualification"
    node.extraction_enabled = True
    node.extraction_prompt = ""
    node.extraction_variables = [
        ExtractionVariableDTO(name="state", type="string", prompt="US state")
    ]
    engine._variable_extraction_manager = MagicMock()
    engine._variable_extraction_manager._perform_extraction = AsyncMock(
        return_value={}
    )

    await engine._perform_variable_extraction_if_needed(
        node, run_in_background=False, extract_disposition=False
    )

    asked_for = [v.name for v in engine._variable_extraction_manager
                 ._perform_extraction.await_args.args[0]]
    assert asked_for == ["state"]


@pytest.mark.asyncio
async def test_the_final_extraction_asks_for_the_disposition():
    engine = PipecatEngine(workflow=None, call_context_vars={})
    node = MagicMock()
    node.name = "General Close"
    node.extraction_enabled = True
    node.extraction_prompt = ""
    node.extraction_variables = [
        ExtractionVariableDTO(name="state", type="string", prompt="US state")
    ]
    engine._current_node = node
    engine._variable_extraction_manager = MagicMock()
    engine._variable_extraction_manager._perform_extraction = AsyncMock(
        return_value={}
    )

    await engine.perform_final_variable_extraction()

    asked_for = [v.name for v in engine._variable_extraction_manager
                 ._perform_extraction.await_args.args[0]]
    assert asked_for == ["state", CALL_DISPOSITION_VARIABLE]
