"""Voice-prompting reference content, available in full or by topic."""

from api.services.voice_prompting_guide._base import VoicePromptingTopic
from api.services.voice_prompting_guide._registry import (
    get_topic,
    list_topic_index,
    render_guide,
)

__all__ = ["VoicePromptingTopic", "get_topic", "list_topic_index", "render_guide"]
