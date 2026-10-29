"""Authoring validation for greetings, separate from legacy runtime parsing."""

from api.services.workflow.dto import NodeType
from api.services.workflow.errors import ItemKind, WorkflowError


def validate_greeting_recordings(
    workflow_definition: dict | None,
) -> list[WorkflowError]:
    if not workflow_definition:
        return []
    nodes = workflow_definition.get("nodes")
    if not isinstance(nodes, list):
        return []

    errors: list[WorkflowError] = []
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != NodeType.startNode.value:
            continue
        data = node.get("data")
        if not isinstance(data, dict) or data.get("greeting_type") != "audio":
            continue
        recording_id = data.get("greeting_recording_id")
        if recording_id is None or (
            isinstance(recording_id, str) and not recording_id.strip()
        ):
            node_id = node.get("id")
            errors.append(
                WorkflowError(
                    kind=ItemKind.node,
                    id=node_id if isinstance(node_id, str) else None,
                    field="data.greeting_recording_id",
                    message="Select a greeting recording for pre-recorded audio",
                )
            )
    return errors
