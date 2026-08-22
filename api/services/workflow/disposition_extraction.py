"""Derive a call's business outcome from the conversation at teardown.

``gathered_context`` carries two different kinds of answer about a finished
call, and conflating them is what made every end-node call read as qualified:

``call_status``
    The *mechanism* -- how the call terminated. ``user_hangup``,
    ``voicemail_detected``, ``no_answer``. Always known, never inferred.

``call_disposition``
    The *outcome* -- what happened commercially. ``not_interested``,
    ``wrong_number``. This is what a dialer reads as a lead status, and it
    drives one decision: call this person again or not.

The engine stamps the mechanism into both the moment teardown starts, so a run
whose pipeline is killed still records something. This module supplies the
variable that lets the extraction LLM *refine* the disposition afterwards, and
the rules for when that refinement is allowed to apply.

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

DEFAULT_DISPOSITION_PROMPT = (
    "Why the call ended, from the caller's side. Use one of: "
    "qualified (the call achieved its goal), "
    "not_interested, "
    "wrong_number, "
    "voicemail (the call reached an answering machine rather than a person), "
    "do_not_call (the caller asked not to be contacted again), "
    "callback_requested. "
    "Answer 'unknown' if the conversation does not clearly show one of these -- "
    "do not guess."
)

#: Teardown reasons whose disposition is a placeholder rather than an answer,
#: and may therefore be replaced by an extracted outcome.
#:
#: ``USER_QUALIFIED`` is stamped for reaching *any* end node, so it asserts
#: success even for a wrong number. ``USER_HANGUP`` records only that the
#: caller went away, which tells a dialer nothing it can act on.
#:
#: Everything absent from this set is already a real answer decided by
#: something other than a language model -- a detector verdict
#: (``VOICEMAIL_DETECTED``), an action Dograh took (``CALL_TRANSFERRED``), an
#: author's declared end-call reason -- and is never second-guessed here.
REFINABLE_REASONS = frozenset(
    {
        EndTaskReason.USER_QUALIFIED.value,
        EndTaskReason.USER_HANGUP.value,
    }
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


def inject_disposition_variable(
    variables: Iterable[ExtractionVariableDTO],
) -> list[ExtractionVariableDTO]:
    """Append the disposition variable unless the author already declared one.

    Author intent wins: a node carrying its own ``call_disposition`` variable
    keeps that prompt and that vocabulary, and nothing is added.
    """
    variables = list(variables)
    if any(v.name == CALL_DISPOSITION_VARIABLE for v in variables):
        return variables
    return [*variables, build_disposition_variable()]


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
