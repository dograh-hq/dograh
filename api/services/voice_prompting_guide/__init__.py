"""Voice-prompting guide: atoms × stage lenses, surfaced to the LLM
that authors Dograh voice workflows.

The atom is the unit of guidance. Each atom is registered once; the
resolver assembles stage briefings on demand. See `_base.py` for the
schema and `_registry.py` for the briefing logic.
"""

from api.services.voice_prompting_guide._base import (
    AuditCheck,
    ReviewSignal,
    Stage,
    StageLens,
    VoicePromptingTopic,
)
from api.services.voice_prompting_guide._registry import (
    build_briefing,
    get_topic,
    list_topic_index,
)

__all__ = [
    "AuditCheck",
    "ReviewSignal",
    "Stage",
    "StageLens",
    "VoicePromptingTopic",
    "build_briefing",
    "get_topic",
    "list_topic_index",
]
