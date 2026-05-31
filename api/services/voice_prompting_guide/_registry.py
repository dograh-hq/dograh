"""Topic registry + briefing resolver.

Stage briefings are *generated* from the registered atoms; they are
never hand-edited. That guarantees lenses, content, and signals stay
in lock-step with their canonical topic file.
"""

from __future__ import annotations

from typing import Optional

from api.services.voice_prompting_guide._base import (
    Stage,
    VoicePromptingTopic,
)
from api.services.voice_prompting_guide.topics import (
    call_flow_design,
    disfluencies,
    end_call_logic,
    guardrails,
    instruction_collision,
    language_and_format,
    numbers_dates_money,
    persona_and_identity_lock,
    readback_and_extraction,
    response_style,
    speech_handling,
    success_criteria,
    tool_calls,
    turn_taking,
)

_TOPICS: dict[str, VoicePromptingTopic] = {}


def _register(topic: VoicePromptingTopic) -> None:
    if topic.id in _TOPICS:
        raise ValueError(
            f"Duplicate voice-prompting topic id: {topic.id!r}. "
            f"Each atom must be registered exactly once."
        )
    _TOPICS[topic.id] = topic


# Registration order is the briefing display order. Roughly: the
# global-behavior cluster first (persona, style, guardrails, format),
# then node-specific authoring topics (flow, readback, numbers, tools,
# success criteria, end-call), then the cross-cutting review checks.
_register(persona_and_identity_lock.TOPIC)
_register(response_style.TOPIC)
_register(disfluencies.TOPIC)
_register(guardrails.TOPIC)
_register(language_and_format.TOPIC)
_register(speech_handling.TOPIC)
_register(call_flow_design.TOPIC)
_register(readback_and_extraction.TOPIC)
_register(numbers_dates_money.TOPIC)
_register(tool_calls.TOPIC)
_register(success_criteria.TOPIC)
_register(end_call_logic.TOPIC)
_register(turn_taking.TOPIC)
_register(instruction_collision.TOPIC)


_STAGE_INTROS: dict[Stage, str] = {
    Stage.plan: (
        "Plan stage. Decide persona, call goal, ordered node list, edges, "
        "exit conditions, and tools/credentials needed. Do not draft prompts "
        "yet — that is the create stage. Keep things simple in first version. "
        "Subtract scope ruthlessly."
    ),
    Stage.create: (
        "Create stage. Write the prompts and emit SDK TypeScript. For each "
        "node type, also call get_node_type to learn its property schema."
    ),
    Stage.review: (
        "Review stage. After saving, inspect any tips[] returned and surface "
        "them to the user. Read prompts looking for instruction collisions "
        "(global vs. node) and missing handoff cues."
    ),
}


def list_topic_index() -> list[dict[str, str]]:
    """Flat index of every topic — used when the caller passes no args."""
    return [{"id": t.id, "title": t.title} for t in _TOPICS.values()]


def get_topic(topic_id: str) -> Optional[VoicePromptingTopic]:
    return _TOPICS.get(topic_id)


def build_briefing(
    stage: Stage,
    node_type: Optional[str] = None,
) -> dict:
    """Assemble the stage briefing: intro + relevant topics with lenses.

    A topic is included when (a) its stage lens is marked relevant, and
    (b) its `applies_to_node_types` either is empty (cross-cutting) or
    includes `node_type`. Topics are returned in registration order so
    the same call yields a stable response.
    """
    topics = [
        t
        for t in _TOPICS.values()
        if t.lens_for(stage) is not None and t.is_relevant_to(node_type)
    ]

    out: dict = {
        "stage": stage.value,
        "intro": _STAGE_INTROS[stage],
        "topics": [t.to_briefing_dict(stage) for t in topics],
        "drill_in": (
            "Call get_voice_prompting_guide(topic='<id>') for the full content "
            "of any topic that materially shapes the prompt you're writing."
        ),
    }
    if node_type is not None:
        out["filtered_to_node_type"] = node_type
    return out
