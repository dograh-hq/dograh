from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkflowRunInputs:
    definition_id: int | None
    initial_context: dict[str, Any]


def _published_definition(workflow) -> object | None:
    return getattr(workflow, "released_definition", None) or getattr(
        workflow, "current_definition", None
    )


async def prepare_workflow_run_inputs(
    workflow_client,
    workflow,
    *,
    initial_context: dict[str, Any] | None = None,
    use_draft: bool = False,
    include_template_context: bool = False,
) -> WorkflowRunInputs:
    """Resolve definition binding and optional template defaults for a run.

    Draft and template-context handling belong at runtime call sites, not in the
    persistence client. Callers must opt in explicitly for workflow-editor/test
    flows.
    """
    target_definition = None
    if use_draft:
        target_definition = await workflow_client.get_draft_version(workflow.id)

    if target_definition is None:
        target_definition = _published_definition(workflow)

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
    )
