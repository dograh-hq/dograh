"""Render the complete voice guide or a single topic from the same content."""

from api.services.voice_prompting_guide._base import VoicePromptingTopic
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

# Insertion order is the order of sections in the complete guide.
_TOPICS = {
    topic.id: topic
    for topic in (
        common_guideliines.TOPIC,
        guardrails.TOPIC,
        call_flow_design.TOPIC,
        tool_calls.TOPIC,
        success_criteria.TOPIC,
        end_call_logic.TOPIC,
        turn_taking.TOPIC,
        instruction_collision.TOPIC,
    )
}


def list_topic_index() -> list[dict[str, str]]:
    return [{"id": topic.id, "title": topic.title} for topic in _TOPICS.values()]


def get_topic(topic_id: str) -> VoicePromptingTopic | None:
    return _TOPICS.get(topic_id)


def render_guide(topic: str | None = None) -> str:
    """Return all sections or one topic; unknown topic IDs raise KeyError."""
    topics = _TOPICS.values() if topic is None else [_TOPICS[topic]]
    return "\n\n".join(
        f"## {item.title} ({item.id})\n\n{item.content.strip()}" for item in topics
    )
