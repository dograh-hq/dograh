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
    user_recording_url: str | None = None
    bot_recording_url: str | None = None
    transcript_public_url: str | None = None
    recording_public_url: str | None = None
    user_recording_public_url: str | None = None
    bot_recording_public_url: str | None = None
    public_access_token: str | None = None
    cost_info: Dict[str, Any] | None
    usage_info: Dict[str, Any] | None = None
    definition_id: int | None  # This is for backward compatibility
    initial_context: dict | None = None
    gathered_context: dict | None = None
    call_type: CallType
    logs: Dict[str, Any] | None = None
    annotations: Dict[str, Any] | None = None
    call_id: str | None = None
    scenario_id: str | None = None
    scenario_name: str | None = None
    service_user_id: str | None = None
    caller_identifier_id: str | None = None
    caller_state: str | None = None
    caller_identifier: str | None = None
    telephone_number: str | None = None
    direction: str | None = None
    started_at: datetime | None = None
    connected_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    call_status: str | None = None
    telephony_provider: str | None = None
    provider_call_id: str | None = None
    model_provider: str | None = None
    stt_provider: str | None = None
    tts_provider: str | None = None
    avatar_provider: str | None = None
    recording_object_key: str | None = None
    recording_duration_seconds: float | None = None
    recording_format: str | None = None
    recording_size_bytes: int | None = None
    full_transcript: str | None = None
    transcript_object_key: str | None = None
    latency_metrics: Dict[str, Any] | None = None
    termination_reason: str | None = None
