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
so a run whose pipeline is killed still records something. When a workflow has
configured call dispositions, the engine makes one dedicated final
extraction request to refine that fallback. It is deliberately separate from
node variable extraction: dispositions describe the whole call, not the node
that happened to be active when it ended.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence

from pipecat.utils.enums import EndTaskReason

from api.schemas.workflow_configurations import CallDispositionOption
from api.services.workflow.dto import ExtractionVariableDTO

#: Internal extraction-variable name used by the dedicated terminal request.
#: This is engine-owned; node extraction variables may not set a call outcome.
CALL_DISPOSITION_VARIABLE = "call_disposition"

#: Mechanical termination fields are populated by ``end_call_with_reason``.
#: They are facts observed by the engine, never values for an LLM to infer.
CALL_STATUS_CONTEXT_KEY = "call_status"
END_REASON_CONTEXT_KEY = "end_reason"

_ENGINE_DERIVED_VARIABLES = frozenset(
    {
        CALL_STATUS_CONTEXT_KEY,
        END_REASON_CONTEXT_KEY,
        CALL_DISPOSITION_VARIABLE,
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

#: Dograh's built-in business outcomes. This is a catalog for filters and
#: reporting, not a validation allowlist: a workflow may configure its own
#: values, such as ``call_rescheduled``.
DEFAULT_DISPOSITION_CODES: tuple[str, ...] = tuple(
    code for code, _description in _DEFAULT_DISPOSITION_OPTIONS
)

#: Extracted values that mean "no answer", not an answer. ``unknown`` is what
#: a workflow prompt may offer the model so it can decline instead of guessing.
_NON_ANSWERS = frozenset({"unknown", "unclear", "none", "n/a", "null"})

#: Dispositions are short labels that end up as per-workflow disposition codes
#: and as keys in an organization's disposition mapping. A model that answers
#: with a sentence has misunderstood the variable; take the floor instead of
#: writing prose into a lead status.
_MAX_DISPOSITION_LENGTH = 64


def build_disposition_variable(
    options: Sequence[CallDispositionOption],
) -> ExtractionVariableDTO:
    """Build the engine-owned variable from a closed disposition list."""
    choices = json.dumps(
        [option.model_dump() for option in options],
        ensure_ascii=False,
        indent=2,
    )
    prompt = (
        f"Set {CALL_DISPOSITION_VARIABLE} to exactly one code from the configured "
        "options below. Return the code exactly as written, not its description. "
        f"If the conversation does not support any option, set "
        f"{CALL_DISPOSITION_VARIABLE} to null. Never invent a new code.\n\n"
        f"Configured dispositions (JSON):\n{choices}"
    )
    return ExtractionVariableDTO(
        name=CALL_DISPOSITION_VARIABLE,
        type="string",
        prompt=prompt,
    )


def prepare_extraction_variables(
    variables: list[ExtractionVariableDTO] | None,
) -> list[ExtractionVariableDTO]:
    """Return node variables that are safe for ordinary extraction.

    Mechanical fields and the call disposition belong to the engine. In
    particular, a node variable named ``call_disposition`` is ignored:
    workflow-level call settings now own the final outcome options.
    """
    return [v for v in variables or [] if v.name not in _ENGINE_DERIVED_VARIABLES]


def coerce_disposition(
    value: object, allowed_codes: Iterable[str] | None = None
) -> str | None:
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
    if allowed_codes is not None:
        canonical_codes = {code.casefold(): code for code in allowed_codes}
        return canonical_codes.get(disposition.casefold())
    if disposition.casefold() in _NON_ANSWERS:
        return None
    return disposition
