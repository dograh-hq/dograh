from datetime import datetime
from typing import Any, Dict

from pydantic import BaseModel

from api.enums import CallType


class WorkflowRunResponseSchema(BaseModel):
    id: int
    workflow_id: int
    name: str
    mode: str
    created_at: datetime
    is_completed: bool
    transcript_url: str | None
    recording_url: str | None
    cost_info: Dict[str, Any] | None
    definition_id: int | None  # This is for backward compatibility
    initial_context: dict | None = None
    gathered_context: dict | None = None
    call_type: CallType
    logs: Dict[str, Any] | None = None
