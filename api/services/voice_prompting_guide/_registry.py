"""Topic registry + briefing resolver.

Stage briefings are *generated* from the registered atoms; they are
never hand-edited. That guarantees lenses, content, and signals stay
in lock-step with their canonical topic file.
"""

from __future__ import annotations

from api.services.voice_prompting_guide._base import (
    Stage,
    VoicePromptingTopic,
)
from api.services.voice_prompting_guide.topics import (
    call_flow_design,
    common_guideliines,
    end_call_logic,
    guardrails,
    instruction_collision,
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


# Registration order is the briefing display order.
_register(common_guideliines.TOPIC)
_register(guardrails.TOPIC)
_register(call_flow_design.TOPIC)
_register(tool_calls.TOPIC)
_register(success_criteria.TOPIC)
_register(end_call_logic.TOPIC)
_register(turn_taking.TOPIC)
_register(instruction_collision.TOPIC)


_STAGE_INTROS: dict[Stage, str] = {
    Stage.plan: (
        "Business discovery belongs to get_workflow_authoring_guide(stage='plan'). "
        "These optional voice lenses help clarify the call's intended behavior: "
        "what information the caller provides, what actions should happen and "
        "when, which business rules constrain them, and what success or failure "
        "looks like. Reuse settled answers. Leave technical design and prompt "
        "writing to the build stage."
    ),
    Stage.create: (
        "Create stage. Turn the plan into prompts and SDK TypeScript. Build "
        "nodes around the information the call must capture, grouping related "
        "fields into one node when that keeps the conversation natural. Make "
        "transition instructions explicit: if an edge is labeled 'Move to "
        "Rental Details', the prompt should tell the agent when to call the "
        "matching tool, such as 'move_to_rental_details'. For each node type, "
        "call get_node_type to learn its property schema before emitting it."
    ),
    Stage.review: (
        "Review stage. Check that the workflow captures the information the "
        "builder wanted and that each prompt names the conditions for moving "
        "to the next node. Read prompts for global-vs-node instruction "
        "collisions, missing handoff cues, and transitions that depend on "
        "unstated business rules."
    ),
}


def list_topic_index() -> list[dict[str, str]]:
    """Flat index of every topic — used when the caller passes no args."""
    return [{"id": t.id, "title": t.title} for t in _TOPICS.values()]


def get_topic(topic_id: str) -> VoicePromptingTopic | None:
    return _TOPICS.get(topic_id)


def build_briefing(
    stage: Stage,
    node_type: str | None = None,
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
            "of each applicable topic marked required_read, and any other topic "
            "that materially shapes the prompt you're writing."
        ),
    }
    if node_type is not None:
        out["filtered_to_node_type"] = node_type
    return out
