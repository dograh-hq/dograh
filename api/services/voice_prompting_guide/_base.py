"""Schema for voice-prompting guidance atoms.

Each `VoicePromptingTopic` is one self-contained piece of advice (e.g.
turn-taking, persona lock, readback rules). The same atom is surfaced
to the LLM through several channels — node `llm_hint`s, the
`get_voice_prompting_guide` tool, save-time lint tips, and the
`/audit_voice_prompts` reviewer — without copying the body anywhere.
Everything else references a topic by `id` and quotes at most one line.

Stage lenses are short framings (1–3 lines) of how the same atom matters
during plan vs. create vs. review. They are NOT a second copy of the
content; they tell the agent where to point its attention at that stage.

`review_signals` are mechanical regex checks over prompt-field text
only — safe to fire on every save. `audit_checks` are intent-level
questions that need LLM judgment and only run under the user-invoked
audit flow. The two are kept separate because conflating "prompt
literally ends with '?'" with "prompt instructs the agent to ask a
question" yields garbage tips.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Stage(str, Enum):
    """Authoring stages. Drives briefing assembly in the resolver."""

    plan = "plan"
    create = "create"
    review = "review"


class StageLens(BaseModel):
    """A topic's framing for one stage. Either marked irrelevant, or
    carries 1–3 lines of stage-specific guidance pointing at the atom's
    full content."""

    relevant: bool = False
    lens: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class ReviewSignal(BaseModel):
    """Mechanical detector — regex over literal prompt text.

    Use only for surface-level issues (markdown in a voice prompt,
    digits where spoken form is needed, persona missing from global).
    Never for runtime behavior the prompt is *meant to produce* — that
    belongs in `audit_checks`.
    """

    id: str
    pattern: str = Field(
        ...,
        description="Python regex applied to prompt-field text.",
    )
    quote: str = Field(
        ...,
        description="One-line user-facing tip when the pattern matches.",
    )

    model_config = ConfigDict(extra="forbid")


class AuditCheck(BaseModel):
    """Intent-level check — requires LLM judgment via `/audit_voice_prompts`.

    The judge agent answers `judge_question` yes/no against the prompt
    being audited; a result that differs from `expected` is a finding.
    """

    id: str
    judge_question: str
    expected: Literal["yes", "no"] = "yes"
    quote: str

    model_config = ConfigDict(extra="forbid")


class VoicePromptingTopic(BaseModel):
    """One atom of voice-prompting guidance.

    `content` is the single source of truth. Lenses, llm_hints, signals,
    and checks reference this atom by `id`; they do not duplicate the
    content text.
    """

    id: str
    title: str
    severity: Literal["low", "medium", "high"] = "medium"
    applies_to_node_types: tuple[str, ...] = Field(default_factory=tuple)
    stages: dict[Stage, StageLens] = Field(default_factory=dict)
    content: str = Field(..., min_length=1)
    review_signals: tuple[ReviewSignal, ...] = Field(default_factory=tuple)
    audit_checks: tuple[AuditCheck, ...] = Field(default_factory=tuple)
    cross_refs: tuple[str, ...] = Field(default_factory=tuple)

    model_config = ConfigDict(extra="forbid")

    def lens_for(self, stage: Stage) -> Optional[str]:
        sl = self.stages.get(stage)
        if sl is None or not sl.relevant:
            return None
        return sl.lens

    def is_relevant_to(self, node_type: Optional[str]) -> bool:
        if node_type is None:
            return True
        # An atom with no `applies_to_node_types` is treated as
        # cross-cutting (relevant to every node type).
        if not self.applies_to_node_types:
            return True
        return node_type in self.applies_to_node_types

    def to_briefing_dict(self, stage: Stage) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "lens": self.lens_for(stage) or "",
        }

    def to_deep_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "severity": self.severity,
            "content": self.content,
            "stages_relevant": [
                stage.value for stage, sl in self.stages.items() if sl.relevant
            ],
        }
        if self.applies_to_node_types:
            out["applies_to_node_types"] = list(self.applies_to_node_types)
        if self.cross_refs:
            out["cross_refs"] = list(self.cross_refs)
        return out
