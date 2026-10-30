from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.services.workflow.authoring.contracts import CallBrief


class BuilderPlanDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    brief_revision: int = Field(ge=1, strict=True)
    decision: Literal["approve", "revise"]
    feedback: str = Field(default="", max_length=12000)

    @model_validator(mode="after")
    def check_feedback(self):
        if self.decision == "revise" and not self.feedback:
            raise ValueError("Describe the changes you want to the plan")
        if self.decision == "approve" and self.feedback:
            raise ValueError("Request changes to send feedback before building")
        return self


class BuilderPlanApproval(BuilderPlanDecision):
    interrupt_id: str = Field(min_length=1)


class BuilderToolDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approve", "deny"]
    feedback: str = Field(default="", max_length=12000)

    @model_validator(mode="after")
    def check_feedback(self):
        if self.decision == "approve" and self.feedback:
            raise ValueError("Approve commits the tool as shown; deny to send feedback")
        return self


class BuilderToolApproval(BuilderToolDecision):
    interrupt_id: str = Field(min_length=1)


class BuilderQuestion(BaseModel):
    id: str
    title: str
    kind: Literal["single", "multiple", "text"]
    options: list[str] = Field(default_factory=list)
    allow_custom: bool = True


class BuilderInterrupt(BaseModel):
    id: str
    kind: Literal["questions", "plan_approval", "mcp", "tool_approval"]
    brief: CallBrief | None = None
    brief_revision: int | None = None
    questions: list[BuilderQuestion] = Field(default_factory=list)
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    # tool_approval: a reusable tool the agent proposes to create or edit,
    # gated for human approval before it is committed to the account.
    operation: Literal["create", "update"] | None = None
    tool: dict[str, Any] | None = None
    tool_uuid: str | None = None
    plain_language: str | None = None
    # Number of workflows that reference the tool being edited; null until the
    # count query ships. The card still warns that edits affect every workflow.
    used_by_count: int | None = None


class BuilderMessage(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str


class BuilderProposal(BaseModel):
    name: str
    code: str
    summary: str
    workflow: dict[str, Any]


class BuilderSession(BaseModel):
    id: UUID
    checkpoint: str | None = None
    messages: list[BuilderMessage] = Field(default_factory=list)
    pending: list[BuilderInterrupt] = Field(default_factory=list)
    proposal: BuilderProposal | None = None
    can_continue: bool = False
    model: str | None = None
    # Set when the session is editing an existing workflow (vs creating one).
    workflow_id: int | None = None


class BuilderTurnRequest(BaseModel):
    request_id: UUID
    checkpoint: str | None = None
    message: str | None = Field(default=None, min_length=1, max_length=12000)
    answers: dict[str, dict[str, list[str]]] | None = None
    approval: BuilderPlanApproval | None = None
    tool_approval: BuilderToolApproval | None = None
    # Present on edit sessions: the workflow being revised. Seeds the build
    # stage from its live code and routes the save to update it in place.
    workflow_id: int | None = None

    @model_validator(mode="after")
    def exclusive_input(self):
        if (
            sum(
                value is not None
                for value in (
                    self.message,
                    self.answers,
                    self.approval,
                    self.tool_approval,
                )
            )
            > 1
        ):
            raise ValueError(
                "Send a message, question answers, a plan decision, or a tool decision"
            )
        return self


class BuilderSaveRequest(BaseModel):
    checkpoint: str


class BuilderSaveResponse(BaseModel):
    workflow_id: int
