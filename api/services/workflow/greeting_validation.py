"""Authoring validation for greetings, separate from legacy runtime parsing."""

from api.services.workflow.dto import NodeType, ReactFlowDTO
from api.services.workflow.errors import ItemKind, WorkflowError


def validate_greeting_recordings(workflow: ReactFlowDTO) -> list[WorkflowError]:
    errors = []
    for node in workflow.nodes:
        if node.type != NodeType.startNode.value or node.data.greeting_type != "audio":
            continue
        recording_id = node.data.greeting_recording_id
        if not recording_id or not recording_id.strip():
            errors.append(
                WorkflowError(
                    kind=ItemKind.node,
                    id=node.id,
                    field="data.greeting_recording_id",
                    message="Select a greeting recording for pre-recorded audio",
                )
            )
    return errors
