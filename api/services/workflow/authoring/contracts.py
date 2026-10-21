"""Business handoffs shared by the builder runtime and external MCP hosts."""

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AuthoringStage(str, Enum):
    plan = "plan"
    build = "build"
    review = "review"


class Handoff(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DataOperation(Handoff):
    purpose: Text
    system: Text
    timing: Text
    inputs: list[Text]
    expected_result: Text
    failure_behavior: Text


class CallBrief(Handoff):
    goal: Text
    audience: Text
    direction: Literal["inbound", "outbound", "both"]
    trigger: Text
    information_available_at_start: list[Text] = Field(default_factory=list)
    information_to_collect: list[Text] = Field(default_factory=list)
    conversation_steps: list[Text] = Field(min_length=1)
    business_rules: list[Text] = Field(default_factory=list)
    data_operations: list[DataOperation] = Field(default_factory=list)
    success_criteria: list[Text] = Field(min_length=1)
    exit_conditions: list[Text] = Field(min_length=1)
    acceptance_scenarios: list[Text] = Field(min_length=1)
    assumptions: list[Text] = Field(default_factory=list)
    open_questions: list[Text] = Field(default_factory=list)


class BuildResult(Handoff):
    code: Text
    summary: Text
    configuration_gaps: list[Text] = Field(default_factory=list)


class ReviewResult(Handoff):
    verdict: Literal["ready", "revise", "needs_input"]
    summary: Text
    blocking_findings: list[Text] = Field(default_factory=list)
    advisories: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_verdict(self):
        if (self.verdict == "ready") == bool(self.blocking_findings):
            raise ValueError(
                "Ready requires no blockers; other verdicts require findings"
            )
        return self
