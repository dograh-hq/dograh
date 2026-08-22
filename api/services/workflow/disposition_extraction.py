"""Derive a call's business outcome from the conversation at teardown.

``gathered_context`` carries two different kinds of answer about a finished
call, and conflating them is what made every end-node call read as qualified:

``call_status``
    The *mechanism* -- how the call terminated. ``user_hangup``,
    ``end_call``, ``voicemail_detected``, ``no_answer``. Always known, never
    inferred.

``call_disposition``
    The *outcome* -- what happened commercially. ``not_interested``,
    ``wrong_number``. This is what a dialer reads as a lead status, and it
    drives one decision: call this person again or not.

The engine records the status and a conservative disposition before teardown,
so a run whose pipeline is killed still records something. This module supplies
the variable that lets the extraction LLM *refine* the disposition afterwards,
and the rules for when that refinement is allowed to apply.

The vocabulary is deliberately not validated here. The default prompt below
names the outcomes Dograh expects, but a workflow author can edit that prompt
-- or replace the variable entirely -- to record whatever their business calls
an outcome. Whatever comes back is what the lead gets.
"""

from __future__ import annotations

from typing import Iterable

from pipecat.utils.enums import EndTaskReason

from api.services.workflow.dto import ExtractionVariableDTO

#: Extraction-variable name the engine reads the refined outcome from. An
#: author who defines a variable of this name on a node owns it outright: the
#: seed below is not injected, and their prompt decides the vocabulary.
CALL_DISPOSITION_VARIABLE = "call_disposition"

#: Mechanical termination fields are populated by ``end_call_with_reason``.
#: They are facts observed by the engine, never values for an LLM to infer.
CALL_STATUS_CONTEXT_KEY = "call_status"
END_REASON_CONTEXT_KEY = "end_reason"

_ENGINE_DERIVED_VARIABLES = frozenset(
    {
        CALL_STATUS_CONTEXT_KEY,
        END_REASON_CONTEXT_KEY,
        "mapped_call_disposition",
        "call_tags",
    }
)

_DEFAULT_DISPOSITION_OPTIONS: tuple[tuple[str, str | None], ...] = (
    ("qualified", "the call achieved its goal"),
    ("not_interested", None),
    ("wrong_number", None),
    (
        EndTaskReason.VOICEMAIL_DETECTED.value,
        "the call reached an answering machine rather than a person",
    ),
    ("do_not_call", "the caller asked not to be contacted again"),
    ("callback_requested", None),
)

#: Dograh's built-in business outcomes. This is the default extraction
#: vocabulary, not a validation allowlist: workflow authors may still supply
#: their own values through a custom ``call_disposition`` variable.
DEFAULT_DISPOSITION_CODES: tuple[str, ...] = tuple(
    code for code, _description in _DEFAULT_DISPOSITION_OPTIONS
)

_DEFAULT_DISPOSITION_OPTIONS_TEXT = ", ".join(
    f"{code} ({description})" if description else code
    for code, description in _DEFAULT_DISPOSITION_OPTIONS
)

DEFAULT_DISPOSITION_PROMPT = (
    "Why the call ended, from the caller's side. Use one of: "
    f"{_DEFAULT_DISPOSITION_OPTIONS_TEXT}. "
    "Answer 'unknown' if the conversation does not clearly show one of these -- "
    "do not guess."
)

#: Extracted values that mean "no answer", not an answer. ``unknown`` is what
#: the default prompt offers the model so it can decline instead of guessing.
_NON_ANSWERS = frozenset({"unknown", "unclear", "none", "n/a", "null"})

#: Dispositions are short labels that end up as per-workflow disposition codes
#: and as keys in an organization's disposition mapping. A model that answers
#: with a sentence has misunderstood the variable; take the floor instead of
#: writing prose into a lead status.
_MAX_DISPOSITION_LENGTH = 64


def build_disposition_variable() -> ExtractionVariableDTO:
    """The disposition variable injected when an author has not defined one."""
    return ExtractionVariableDTO(
        name=CALL_DISPOSITION_VARIABLE,
        type="string",
        prompt=DEFAULT_DISPOSITION_PROMPT,
    )


def prepare_extraction_variables(
    variables: Iterable[ExtractionVariableDTO],
    *,
    include_disposition: bool,
) -> list[ExtractionVariableDTO]:
    """Return only the variables the LLM should extract for this phase.

    ``call_status`` and its legacy ``end_reason`` alias come from pipeline
    mechanics and are therefore always removed. ``call_disposition`` is held
    until the terminal extraction, where the author's custom prompt is reused
    if they supplied one; otherwise Dograh's default variable is appended.
    """
    variables = list(variables)
    author_disposition = next(
        (v for v in variables if v.name == CALL_DISPOSITION_VARIABLE), None
    )
    extractable = [
        v
        for v in variables
        if v.name not in _ENGINE_DERIVED_VARIABLES
        and v.name != CALL_DISPOSITION_VARIABLE
    ]
    if include_disposition:
        extractable.append(author_disposition or build_disposition_variable())
    return extractable


def coerce_disposition(value: object) -> str | None:
    """Normalise an extracted disposition, or ``None`` if it is not usable.

    ``None`` means "keep whatever the engine already recorded". Returning the
    floor rather than a half-parsed value is what keeps a confused extraction
    from being written to a lead.
    """
    if not isinstance(value, str):
        # Extraction returns whatever JSON the model produced; a dict or a list
        # here means it answered a different question than the one asked.
        return None

    disposition = value.strip()
    if not disposition or len(disposition) > _MAX_DISPOSITION_LENGTH:
        return None
    if disposition.casefold() in _NON_ANSWERS:
        return None
    return disposition
