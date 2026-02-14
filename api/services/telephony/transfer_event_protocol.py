"""Redis communication protocol for call transfer coordination.

Defines event formats and Redis channels for coordinating call transfers
across multiple API server instances.
"""

import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Optional


class TransferEventType(str, Enum):
    """Types of transfer events sent between instances."""

    TRANSFER_INITIATED = "transfer_initiated"
    TRANSFER_ANSWERED = "transfer_answered"
    TRANSFER_COMPLETED = "transfer_completed"
    TRANSFER_FAILED = "transfer_failed"
    TRANSFER_CANCELLED = "transfer_cancelled"
    TRANSFER_TIMEOUT = "transfer_timeout"


@dataclass
class TransferEvent:
    """Event data structure for transfer coordination."""

    type: TransferEventType
    transfer_id: str
    original_call_sid: str
    transfer_call_sid: Optional[str] = None
    target_number: Optional[str] = None
    conference_name: Optional[str] = None
    message: Optional[str] = None
    status: Optional[str] = None
    action: Optional[str] = None
    reason: Optional[str] = None
    end_call: bool = False
    timestamp: Optional[float] = None

    def to_json(self) -> str:
        """Convert event to JSON string."""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> "TransferEvent":
        """Create event from JSON string."""
        return cls(**json.loads(data))

    def to_result_dict(self) -> Dict[str, Any]:
        """Convert to function call result format."""
        result = {
            "status": self.status or "success",
            "message": self.message or "",
            "action": self.action or self.type,
            "conference_id": self.conference_name,
            "transfer_call_sid": self.transfer_call_sid,
            "original_call_sid": self.original_call_sid,
            "end_call": self.end_call,
            "reason": self.reason,
        }
        return result


@dataclass
class TransferContext:
    """Transfer context data stored in Redis."""

    transfer_id: str
    call_sid: Optional[str]
    target_number: str
    tool_uuid: str
    original_call_sid: str
    conference_name: str
    initiated_at: float

    def to_json(self) -> str:
        """Convert context to JSON string."""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> "TransferContext":
        """Create context from JSON string."""
        return cls(**json.loads(data))

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


class TransferRedisChannels:
    """Redis channel naming conventions for transfer events."""

    @staticmethod
    def transfer_events(transfer_id: str) -> str:
        """Channel for transfer events for a specific transfer."""
        return f"transfer:events:{transfer_id}"

    @staticmethod
    def transfer_context_key(transfer_id: str) -> str:
        """Redis key for transfer context storage."""
        return f"transfer:context:{transfer_id}"
