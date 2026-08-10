# api/services/workflow/errors.py
from enum import Enum
from typing import TypedDict


class ItemKind(str, Enum):
    node = "node"
    edge = "edge"
    workflow = "workflow"


class WorkflowError(TypedDict):
    kind: ItemKind  # "node" | "edge"
    id: str | None  # nodeId or edgeId
    field: str | None  # “data.prompt”, “position.x”, … (optional)
    message: str  # human-readable text
