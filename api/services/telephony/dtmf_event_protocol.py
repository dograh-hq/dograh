"""Protocol definitions for DTMF events"""

from datetime import datetime
from pydantic import BaseModel, Field


class DTMFEvent(BaseModel):
    """Event triggered when a user presses a keypad digit."""
    call_id: str = Field(description="The call ID associated with the DTMF event.")
    digit: str = Field(description="The single digit pressed (0-9, *, #).")
    timestamp: datetime = Field(description="When the digit was pressed.")

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str) -> "DTMFEvent":
        return cls.model_validate_json(data)


class DTMFRedisChannels:
    """Redis channel naming conventions for DTMF events."""

    @staticmethod
    def dtmf_channel(call_id: str) -> str:
        """Channel for DTMF events for a specific call."""
        return f"telephony:dtmf:{call_id}"
