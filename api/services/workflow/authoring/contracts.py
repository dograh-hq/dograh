"""Business handoffs shared by the builder runtime and external MCP hosts."""

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AuthoringStage(str, Enum):
    build_requirements = "build_requirements"
    plan = "plan"
    build = "build"
    review = "review"


class Handoff(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CallBrief(Handoff):
    goal: Text = Field(
        description="One sentence: what a successful call achieves and for whom."
    )
    direction: Literal["inbound", "outbound", "both"] = Field(
        description="Whether the agent receives calls, places them, or both."
    )
    information_to_collect: list[Text] = Field(
        default_factory=list,
        description=(
            "Each fact the agent must capture from the caller, in the customer's "
            "words, for example 'preferred appointment date'."
        ),
    )
    business_rules: list[Text] = Field(
        default_factory=list,
        description=(
            "Policies only the customer can supply: eligibility, hours, what to do "
            "on refusal, wrong person, or out-of-scope requests, and when to hand "
            "off to a human. State any required data lookup or update here as what "
            "happens and when."
        ),
    )
    success_criteria: list[Text] = Field(
        min_length=1,
        description="Observable outcomes that mark the call as successful.",
    )
    assumptions: list[Text] = Field(
        default_factory=list,
        description=(
            "Optional or deferred details resolved with a sensible default, stated "
            "so the customer can correct them."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_open_questions(cls, value):
        # Older saved approval checkpoints include this removed field.
        if isinstance(value, dict):
            return {key: item for key, item in value.items() if key != "open_questions"}
        return value


class ConversationPlan(Handoff):
    content: Text = Field(
        description=(
            "Conversation flow and complete prompts: shared instructions, named "
            "steps, transition conditions, endings, and values to extract. "
            "Use plain text, not SDK code or graph JSON."
        )
    )
    configuration_gaps: list[Text] = Field(default_factory=list)


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
