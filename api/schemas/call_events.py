"""Destination-neutral configuration exposed through organization preferences."""

from pydantic import BaseModel, Field


class CallEventsSettings(BaseModel):
    enabled: bool = False
    sink_type: str | None = None
    config: dict = Field(default_factory=dict)


class CallEventsConnectionResult(BaseModel):
    message: str
