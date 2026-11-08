from typing import Any

from pydantic import BaseModel, Field

from api.schemas.workflow_configurations import PromptVariablesConfiguration


class PromptPreviewRequest(BaseModel):
    prompt_variables: PromptVariablesConfiguration = Field(
        default_factory=PromptVariablesConfiguration
    )
    sample_values: dict[str, Any] | None = None
    sample_run_id: int | None = None
    workflow_definition: dict | None = None


class NodePromptPreview(BaseModel):
    node_id: str
    node_name: str
    system_prompt: str
    tools: list[dict]
    variables: list[str]
    unresolved_variables: list[str]
    static_tokens_estimate: int
    per_call_tokens_estimate: int
    cache_status: str | None = None


class PromptPreviewResponse(BaseModel):
    nodes: list[NodePromptPreview]
    call_context: str | None
    provider: str | None
    model: str | None
    explicit_cache_available: bool
    minimum_tokens: int | None = None
    notices: list[str]
    default_note: str
    italian_note: str


class PromptReplayRequest(BaseModel):
    prompt_variables: PromptVariablesConfiguration = Field(
        default_factory=lambda: PromptVariablesConfiguration(mode="call_context")
    )
    limit: int = Field(default=3, ge=1, le=10)
    max_turns: int = Field(default=10, ge=1, le=20)


class PromptReplayResponse(BaseModel):
    turns: list[dict[str, Any]]
    valid_turns: int
    failed_turns: int
    action_agreement: float | None
    inline_noise_agreement: float | None
    call_context_noise_agreement: float | None
    notices: list[str]
    semantic_agreement: dict[str, dict[str, float | int | None]]
    sample_metrics: dict[str, dict[str, float | int | None]]
