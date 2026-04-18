from enum import Enum
from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, Field, ValidationError, model_validator


class NodeType(str, Enum):
    startNode = "startCall"
    endNode = "endCall"
    agentNode = "agentNode"
    globalNode = "globalNode"
    trigger = "trigger"
    webhook = "webhook"
    qa = "qa"


class Position(BaseModel):
    x: float
    y: float


class VariableType(str, Enum):
    string = "string"
    number = "number"
    boolean = "boolean"


class ExtractionVariableDTO(BaseModel):
    name: str = Field(..., min_length=1)
    type: VariableType
    prompt: Optional[str] = None


class CustomHeaderDTO(BaseModel):
    key: str
    value: str


class RetryConfigDTO(BaseModel):
    enabled: bool = False
    max_retries: int = 3
    retry_delay_seconds: int = 5


# ─────────────────────────────────────────────────────────────────────────
# Per-type node data classes.
#
# Shared fields are factored out as Pydantic mixins; per-type classes
# inherit only the mixins they need so mistyped fields raise at validation
# time and downstream consumers get accurate types. `is_start` / `is_end`
# live on every variant so the WorkflowGraph can identify boundary nodes
# without dispatching on type.
# ─────────────────────────────────────────────────────────────────────────


class _NodeDataBase(BaseModel):
    name: str = Field(..., min_length=1)
    is_start: bool = False
    is_end: bool = False


class _PromptedNodeDataMixin(BaseModel):
    prompt: Optional[str] = Field(default=None)
    is_static: bool = False
    allow_interrupt: bool = False
    add_global_prompt: bool = True


class _ExtractionNodeDataMixin(BaseModel):
    extraction_enabled: bool = False
    extraction_prompt: Optional[str] = None
    extraction_variables: Optional[list[ExtractionVariableDTO]] = None


class _ToolDocumentRefsMixin(BaseModel):
    tool_uuids: Optional[List[str]] = None
    document_uuids: Optional[List[str]] = None


class StartCallNodeData(
    _NodeDataBase,
    _PromptedNodeDataMixin,
    _ExtractionNodeDataMixin,
    _ToolDocumentRefsMixin,
):
    is_start: bool = True
    greeting: Optional[str] = None
    greeting_type: Optional[str] = None  # 'text' or 'audio'
    greeting_recording_id: Optional[str] = None
    wait_for_user_response: bool = False
    wait_for_user_response_timeout: Optional[float] = None
    detect_voicemail: bool = False
    delayed_start: bool = False
    delayed_start_duration: Optional[float] = None
    pre_call_fetch_enabled: bool = False
    pre_call_fetch_url: Optional[str] = None
    pre_call_fetch_credential_uuid: Optional[str] = None


class AgentNodeData(
    _NodeDataBase,
    _PromptedNodeDataMixin,
    _ExtractionNodeDataMixin,
    _ToolDocumentRefsMixin,
):
    pass


class EndCallNodeData(
    _NodeDataBase,
    _PromptedNodeDataMixin,
    _ExtractionNodeDataMixin,
):
    is_end: bool = True


class GlobalNodeData(_NodeDataBase, _PromptedNodeDataMixin):
    pass


class TriggerNodeData(_NodeDataBase):
    trigger_path: Optional[str] = None
    enabled: bool = True


class WebhookNodeData(_NodeDataBase):
    enabled: bool = True
    http_method: Optional[str] = None
    endpoint_url: Optional[str] = None
    credential_uuid: Optional[str] = None
    custom_headers: Optional[list[CustomHeaderDTO]] = None
    payload_template: Optional[dict] = None
    retry_config: Optional[RetryConfigDTO] = None


class QANodeData(_NodeDataBase):
    qa_enabled: bool = True
    qa_use_workflow_llm: bool = True
    qa_provider: Optional[str] = None
    qa_model: Optional[str] = None
    qa_api_key: Optional[str] = None
    qa_endpoint: Optional[str] = None
    qa_system_prompt: Optional[str] = None
    qa_min_call_duration: int = 15
    qa_voicemail_calls: bool = False
    qa_sample_rate: int = 100


# Union of every per-type data class — useful as a type annotation on
# consumers that handle any node data without dispatching on type. Cannot
# be called as a constructor; use the per-type class directly.
NodeDataDTO = Union[
    StartCallNodeData,
    AgentNodeData,
    EndCallNodeData,
    GlobalNodeData,
    TriggerNodeData,
    WebhookNodeData,
    QANodeData,
]


# ─────────────────────────────────────────────────────────────────────────
# Per-type RF nodes.
#
# RFNodeDTO is a discriminated Union over `type`. Pydantic dispatches to
# the right variant when validating wire JSON. Direct instantiation must
# use the concrete per-type class (StartCallRFNode, AgentRFNode, ...).
# ─────────────────────────────────────────────────────────────────────────


class _RFNodeBase(BaseModel):
    id: str
    position: Position


def _require_prompt(data, type_label: str) -> None:
    prompt = getattr(data, "prompt", None)
    if not prompt or len(prompt.strip()) == 0:
        raise ValueError(f"Prompt is required for {type_label} nodes")


class StartCallRFNode(_RFNodeBase):
    type: Literal["startCall"] = "startCall"
    data: StartCallNodeData

    @model_validator(mode="after")
    def _validate(self):
        _require_prompt(self.data, "start")
        return self


class AgentRFNode(_RFNodeBase):
    type: Literal["agentNode"] = "agentNode"
    data: AgentNodeData

    @model_validator(mode="after")
    def _validate(self):
        _require_prompt(self.data, "agent")
        return self


class EndCallRFNode(_RFNodeBase):
    type: Literal["endCall"] = "endCall"
    data: EndCallNodeData

    @model_validator(mode="after")
    def _validate(self):
        _require_prompt(self.data, "end")
        return self


class GlobalRFNode(_RFNodeBase):
    type: Literal["globalNode"] = "globalNode"
    data: GlobalNodeData

    @model_validator(mode="after")
    def _validate(self):
        _require_prompt(self.data, "global")
        return self


class TriggerRFNode(_RFNodeBase):
    type: Literal["trigger"] = "trigger"
    data: TriggerNodeData


class WebhookRFNode(_RFNodeBase):
    type: Literal["webhook"] = "webhook"
    data: WebhookNodeData


class QARFNode(_RFNodeBase):
    type: Literal["qa"] = "qa"
    data: QANodeData


RFNodeDTO = Annotated[
    Union[
        StartCallRFNode,
        AgentRFNode,
        EndCallRFNode,
        GlobalRFNode,
        TriggerRFNode,
        WebhookRFNode,
        QARFNode,
    ],
    Field(discriminator="type"),
]


# ─────────────────────────────────────────────────────────────────────────
# Edges
# ─────────────────────────────────────────────────────────────────────────


class EdgeDataDTO(BaseModel):
    label: str = Field(..., min_length=1)
    condition: str = Field(..., min_length=1)
    transition_speech: Optional[str] = None
    transition_speech_type: Optional[str] = None  # 'text' or 'audio'
    transition_speech_recording_id: Optional[str] = None


class RFEdgeDTO(BaseModel):
    id: str
    source: str
    target: str
    data: EdgeDataDTO


class ReactFlowDTO(BaseModel):
    nodes: List[RFNodeDTO]
    edges: List[RFEdgeDTO]

    @model_validator(mode="after")
    def _referential_integrity(self):
        node_ids = {n.id for n in self.nodes}
        line_errors: list[dict[str, str]] = []

        for idx, edge in enumerate(self.edges):
            for endpoint in (edge.source, edge.target):
                if endpoint not in node_ids:
                    line_errors.append(
                        dict(
                            loc=("edges", idx),
                            type="missing_node",
                            msg="Edge references missing node",
                            input=edge.model_dump(mode="python"),
                            ctx={"edge_id": edge.id, "endpoint": endpoint},
                        )
                    )

        if line_errors:
            raise ValidationError.from_exception_data(
                title="ReactFlowDTO validation failed",
                line_errors=line_errors,
            )

        return self


# Node type → per-type data class. Keeps sanitize_workflow_definition in
# step with RFNodeDTO's discriminated union.
_NODE_DATA_CLASSES: dict[str, type[BaseModel]] = {
    NodeType.startNode.value: StartCallNodeData,
    NodeType.agentNode.value: AgentNodeData,
    NodeType.endNode.value: EndCallNodeData,
    NodeType.globalNode.value: GlobalNodeData,
    NodeType.trigger.value: TriggerNodeData,
    NodeType.webhook.value: WebhookNodeData,
    NodeType.qa.value: QANodeData,
}


def sanitize_workflow_definition(definition: dict | None) -> dict | None:
    """Strip unknown fields from each node.data and edge.data so UI-only
    runtime state (`invalid`, `validationMessage`, etc.) doesn't leak into
    persisted workflow JSON.

    Only `.data` is filtered — top-level keys on nodes/edges/definition
    (viewport, ReactFlow-computed width/height, etc.) are preserved as-is.
    This is a stripper, not a validator: it doesn't enforce required fields
    or run model_validators, so partial drafts save cleanly.
    """
    if not definition:
        return definition

    out = dict(definition)
    raw_nodes = out.get("nodes")
    if isinstance(raw_nodes, list):
        out["nodes"] = [_sanitize_node(n) for n in raw_nodes]
    raw_edges = out.get("edges")
    if isinstance(raw_edges, list):
        out["edges"] = [_sanitize_edge(e) for e in raw_edges]
    return out


def _sanitize_node(node):
    if not isinstance(node, dict):
        return node
    data_cls = _NODE_DATA_CLASSES.get(node.get("type"))
    raw_data = node.get("data")
    if not data_cls or not isinstance(raw_data, dict):
        return node
    allowed = data_cls.model_fields.keys()
    return {**node, "data": {k: v for k, v in raw_data.items() if k in allowed}}


def _sanitize_edge(edge):
    if not isinstance(edge, dict):
        return edge
    raw_data = edge.get("data")
    if not isinstance(raw_data, dict):
        return edge
    allowed = EdgeDataDTO.model_fields.keys()
    return {**edge, "data": {k: v for k, v in raw_data.items() if k in allowed}}
