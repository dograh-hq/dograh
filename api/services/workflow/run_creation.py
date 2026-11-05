from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkflowRunInputs:
    definition_id: int | None
    initial_context: dict[str, Any]
    use_draft: bool = False


def published_definition(workflow) -> object | None:
    """The live definition of ``workflow``: released, else current."""
    return getattr(workflow, "released_definition", None) or getattr(
        workflow, "current_definition", None
    )


# Retained for callers inside this module predating the public name.
_published_definition = published_definition


async def definition_to_run(
    workflow_client,
    workflow,
    *,
    use_draft: bool = False,
    definition_id: int | None = None,
):
    """The definition a run of ``workflow`` should pin.

    One policy, shared by run creation and by in-call agent transfer, so a
    destination agent runs the same version a fresh call to it would -- draft
    included. A test call running the caller's draft and then handing over to
    another agent's last published version reads as a bug in the destination
    agent, since nothing in the call says a different version answered.

    ``use_draft`` is best-effort by design: a workflow with no draft falls back
    to its live definition rather than failing, which is what makes it safe to
    turn on for a whole call rather than per agent.
    """
    if definition_id is not None:
        if use_draft:
            raise ValueError("An explicit version cannot be combined with use_draft")
        definition = await workflow_client.get_workflow_definition(
            workflow.id, definition_id, workflow.organization_id
        )
        if definition is None or definition.status not in {"published", "archived"}:
            raise ValueError("Published or archived agent version not found")
        return definition
    if use_draft:
        draft = await workflow_client.get_draft_version(workflow.id)
        if draft is not None:
            return draft
    return published_definition(workflow)


async def prepare_workflow_run_inputs(
    workflow_client,
    workflow,
    *,
    initial_context: dict[str, Any] | None = None,
    use_draft: bool = False,
    include_template_context: bool = False,
    definition_id: int | None = None,
) -> WorkflowRunInputs:
    """Resolve definition binding and optional template defaults for a run.

    Draft and template-context handling belong at runtime call sites, not in the
    persistence client. Callers must opt in explicitly for workflow-editor/test
    flows.
    """
    target_definition = await definition_to_run(
        workflow_client, workflow, use_draft=use_draft, definition_id=definition_id
    )

    default_context = {}
    if include_template_context:
        definition_context = (
            getattr(target_definition, "template_context_variables", None)
            if target_definition
            else None
        )
        default_context = (
            definition_context
            if definition_context is not None
            else getattr(workflow, "template_context_variables", None)
        ) or {}

    return WorkflowRunInputs(
        definition_id=getattr(target_definition, "id", None),
        initial_context={
            **default_context,
            **(initial_context or {}),
        },
        use_draft=use_draft,
    )
