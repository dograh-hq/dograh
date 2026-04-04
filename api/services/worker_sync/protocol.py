"""Worker sync event protocol.

Defines the message format for cross-worker state synchronization via Redis pub/sub.
"""

import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Optional

from loguru import logger


class WorkerSyncEventType(str, Enum):
    """Types of worker sync events."""

    LANGFUSE_CREDENTIALS = "langfuse_credentials"


@dataclass
class WorkerSyncEvent:
    """A notification that some shared state has changed.

    Handlers should re-read authoritative state from the DB rather than
    relying on fields in the event — the event is just a trigger.
    """

    event_type: str  # handler key, e.g. "langfuse_credentials"
    action: str  # "update" or "delete"
    org_id: str = ""
    timestamp: Optional[str] = None

    def __post_init__(self):
        if self.timestamp is None:
            from datetime import UTC, datetime

            self.timestamp = datetime.now(UTC).isoformat()

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> Optional["WorkerSyncEvent"]:
        try:
            return cls(**json.loads(data))
        except Exception as e:
            logger.error(f"Failed to parse worker sync event: {e}, data: {data}")
            return None
