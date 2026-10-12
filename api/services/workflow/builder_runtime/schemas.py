from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from api.schemas.workflow_builder import BuilderPlanDecision


def validate_plan_decision(revision: int, value: Any) -> BuilderPlanDecision:
    decision = BuilderPlanDecision.model_validate(value)
    if decision.brief_revision != revision:
        raise ValueError("The plan changed; reload and review it again")
    return decision


class BuilderQuestion(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=500)
    kind: Literal["single", "multiple", "text"] = "single"
    options: list[str] = Field(default_factory=list, max_length=12)
    allow_custom: bool = True

    @model_validator(mode="after")
    def check_options(self):
        if self.kind != "text" and not self.options:
            raise ValueError("Choice questions require options")
        if len(set(self.options)) != len(self.options):
            raise ValueError("Question options must be unique")
        return self


class QuestionBatch(BaseModel):
    questions: list[BuilderQuestion] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError("Question IDs must be unique")
        return self


def validate_answers(questions: list[dict], answers: Any) -> dict[str, list[str]]:
    batch = QuestionBatch(questions=questions)
    if not isinstance(answers, dict) or set(answers) != {q.id for q in batch.questions}:
        raise ValueError("Answer every pending question")
    for question in batch.questions:
        values = answers[question.id]
        if not isinstance(values, list) or not 1 <= len(values) <= 20:
            raise ValueError("Select or enter at least one answer")
        if any(
            not isinstance(v, str) or not v.strip() or len(v) > 2000 for v in values
        ):
            raise ValueError("Answers must contain 1–2000 characters")
        if question.kind != "multiple" and len(values) != 1:
            raise ValueError("This question accepts one answer")
        if (
            not question.allow_custom
            and question.kind != "text"
            and any(v not in question.options for v in values)
        ):
            raise ValueError("Select one of the offered options")
    return answers


class BuilderStep(BaseModel):
    request_id: UUID
    message: str | None = Field(default=None, min_length=1, max_length=12000)
    resume: dict[str, Any] | None = None
    checkpoint: str | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def exclusive_input(self):
        if self.message is not None and self.resume is not None:
            raise ValueError("Send either a message or answers")
        return self
