"""A topic of voice-prompting guidance shared by MCP and the builder."""

from pydantic import BaseModel, ConfigDict, Field


class VoicePromptingTopic(BaseModel):
    id: str
    title: str
    content: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")
