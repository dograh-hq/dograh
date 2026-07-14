from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class Position(BaseModel):
    x: float
    y: float


class NodeData(BaseModel):
    """Flexible model for any Dograh node type."""
    name: str = ""
    prompt: str | None = None
    greeting: str | None = None
    greeting_type: str | None = None
    allow_interrupt: bool = True
    add_global_prompt: bool = True
    extraction_enabled: bool = False
    extraction_prompt: str | None = None
    extraction_variables: list[dict] | None = None
    tool_uuids: list[str] | None = None
    document_uuids: list[str] | None = None
    enabled: bool = True
    model_config = {"extra": "allow"}


class RFNode(BaseModel):
    id: str
    type: str
    position: Position
    data: NodeData = Field(default_factory=NodeData)


class EdgeData(BaseModel):
    label: str = ""
    condition: str = ""
    transition_speech: str | None = None
    model_config = {"extra": "allow"}


class RFEdge(BaseModel):
    id: str
    source: str
    target: str
    data: EdgeData = Field(default_factory=EdgeData)


class WorkflowGraph(BaseModel):
    id: str | None = None
    nodes: list[RFNode]
    edges: list[RFEdge]


class ModelConfig(BaseModel):
    provider: str = "google_realtime"
    model: str = "gemini-2.5-flash-native-audio"
    temperature: float = 0.8
    max_output_tokens: int | None = None
    vertexai: bool = False
    project: str = ""
    location: str = "europe-west1"
    model_config = {"extra": "allow"}


class STTConfig(BaseModel):
    provider: str = "deepgram"
    model_id: str = "nova-3"
    model_config = {"extra": "allow"}


class TTSConfig(BaseModel):
    provider: str = "cartesia"
    model_id: str = "sonic-3"
    voice_id: str = "Kore"
    model_config = {"extra": "allow"}


class ToolDefinition(BaseModel):
    name: str
    type: str = "custom"
    config: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    model_config = {"extra": "allow"}


class RuntimeConfig(BaseModel):
    """Full config returned by Dograh's GET /api/internal/workflows/{id}/runtime-config."""
    workflow_id: int = 0
    org_id: str
    agent_id: str = ""
    agent_name: str = ""
    workflow_graph: WorkflowGraph
    llm_config: ModelConfig = Field(default_factory=ModelConfig)
    stt_config: STTConfig = Field(default_factory=STTConfig)
    tts_config: TTSConfig = Field(default_factory=TTSConfig)
    system_prompt: str = ""
    greeting_message: str = ""
    tools: list[ToolDefinition] = Field(default_factory=list)
    kb_refs: list[str] = Field(default_factory=list)
    handoff_sip_number: str = ""
    channel: str = "voice_sip"
    stages: list[dict] = Field(default_factory=list)
    orchestrator_mode: str = "agentos"

    model_config = {"extra": "allow"}
