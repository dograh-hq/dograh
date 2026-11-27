"""Resolve how a workflow was first created, for analytics attribution.

``created_via`` is stamped once at creation onto the ``workflows`` row and is
never changed afterwards. It must NOT be derived from ``builder_sessions``:
opening the builder on an *existing* agent also writes a ``builder_sessions``
entry (see ``WorkflowClient.get_workflow_builder_session_id``), so a
blank/template agent would be misattributed to the builder the moment someone
edits it there. Origin therefore reflects creation only; "the builder touched
it later" is the separate ``builder_edited`` signal.

See the agent-builder PostHog plan for the full rationale.
"""

CREATED_VIA_BUILDER = "builder"
CREATED_VIA_TEMPLATE = "template"
CREATED_VIA_BLANK = "blank"
CREATED_VIA_UNKNOWN = "unknown"


def resolve_workflow_origin(
    created_via: str | None, has_builder_session: bool
) -> tuple[str, bool]:
    """Return ``(created_via, builder_edited)`` for a workflow.

    ``created_via`` is the stamped ``workflows.created_via`` column (one of
    builder/template/blank, or None for pre-stamp rows). ``has_builder_session``
    is whether the anchor definition carries a ``builder_sessions`` link.

    ``builder_edited`` is True when the builder was used on an agent not born in
    it. Pre-stamp rows fall back to a best-effort guess: a builder-session link
    implies builder origin (we cannot distinguish historical create-vs-edit),
    otherwise ``unknown``.
    """
    if created_via is None:
        created_via = (
            CREATED_VIA_BUILDER if has_builder_session else CREATED_VIA_UNKNOWN
        )
    builder_edited = has_builder_session and created_via != CREATED_VIA_BUILDER
    return created_via, builder_edited
