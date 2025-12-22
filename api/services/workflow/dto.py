from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, ValidationError, model_validator


class NodeType(str, Enum):
    startNode = "startCall"
    endNode = "endCall"
    agentNode = "agentNode"
    globalNode = "globalNode"
    trigger = "trigger"
    webhook = "webhook"


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


class NodeDataDTO(BaseModel):
    name: str = Field(..., min_length=1)
    prompt: Optional[str] = Field(default=None)
    is_static: bool = False
    is_start: bool = False
    is_end: bool = False
    allow_interrupt: bool = False
    extraction_enabled: bool = False
    extraction_prompt: Optional[str] = None
    extraction_variables: Optional[list[ExtractionVariableDTO]] = None
    add_global_prompt: bool = True
    wait_for_user_response: bool = False
    wait_for_user_response_timeout: Optional[float] = None
    detect_voicemail: bool = True
    delayed_start: bool = False
    delayed_start_duration: Optional[float] = None
    trigger_path: Optional[str] = None
    # Webhook node specific fields
    enabled: bool = True
    http_method: Optional[str] = None
    endpoint_url: Optional[str] = None
    credential_uuid: Optional[str] = None
    custom_headers: Optional[list[CustomHeaderDTO]] = None
    payload_template: Optional[dict] = None
    retry_config: Optional[RetryConfigDTO] = None


class RFNodeDTO(BaseModel):
    id: str
    type: NodeType = Field(default=NodeType.agentNode)
    position: Position
    data: NodeDataDTO

    @model_validator(mode="after")
    def _validate_prompt_required(self):
        """Require prompt for all node types except trigger and webhook."""
        if self.type not in (NodeType.trigger, NodeType.webhook):
            if not self.data.prompt or len(self.data.prompt.strip()) == 0:
                raise ValueError("Prompt is required for non-trigger nodes")
        return self


class EdgeDataDTO(BaseModel):
    label: str = Field(..., min_length=1)
    condition: str = Field(..., min_length=1)


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
