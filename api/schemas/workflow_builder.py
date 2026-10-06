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


class BuilderQuestion(BaseModel):
    id: str
    title: str
    kind: Literal["single", "multiple", "text"]
    options: list[str] = Field(default_factory=list)
    allow_custom: bool = True


class BuilderInterrupt(BaseModel):
    id: str
    kind: Literal["questions", "plan_approval", "mcp"]
    brief: CallBrief | None = None
    brief_revision: int | None = None
    questions: list[BuilderQuestion] = Field(default_factory=list)
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


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


class BuilderTurnRequest(BaseModel):
    request_id: UUID
    checkpoint: str | None = None
    message: str | None = Field(default=None, min_length=1, max_length=12000)
    answers: dict[str, dict[str, list[str]]] | None = None
    approval: BuilderPlanApproval | None = None

    @model_validator(mode="after")
    def exclusive_input(self):
        if (
            sum(
                value is not None
                for value in (self.message, self.answers, self.approval)
            )
            > 1
        ):
            raise ValueError("Send a message, question answers, or a plan decision")
        return self


class BuilderSaveRequest(BaseModel):
    checkpoint: str


class BuilderSaveResponse(BaseModel):
    workflow_id: int
