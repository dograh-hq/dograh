"""Campaign event protocol for orchestrator communication.

Defines message formats and helpers for campaign event publishing and handling.
"""

import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Optional


class CampaignEventType(str, Enum):
    """Types of campaign events."""

    # Batch processing events
    BATCH_COMPLETED = "batch_completed"
    BATCH_FAILED = "batch_failed"

    # Sync events
    SYNC_STARTED = "sync_started"
    SYNC_COMPLETED = "sync_completed"
    SYNC_FAILED = "sync_failed"

    # Campaign lifecycle events
    CAMPAIGN_STARTED = "campaign_started"
    CAMPAIGN_PAUSED = "campaign_paused"
    CAMPAIGN_RESUMED = "campaign_resumed"
    CAMPAIGN_COMPLETED = "campaign_completed"
    CAMPAIGN_FAILED = "campaign_failed"

    # Retry events
    RETRY_NEEDED = "retry_needed"
    RETRY_SCHEDULED = "retry_scheduled"
    RETRY_FAILED = "retry_failed"

    # Circuit breaker events
    CIRCUIT_BREAKER_TRIPPED = "circuit_breaker_tripped"


class RetryReason(str, Enum):
    """Reasons for retry."""

    BUSY = "busy"
    NO_ANSWER = "no_answer"
    VOICEMAIL = "voicemail"
    FAILED = "failed"
    ERROR = "error"


@dataclass
class BaseCampaignEvent:
    """Base class for all campaign events."""

    type: str
    campaign_id: int = 0
    timestamp: Optional[str] = None

    def __post_init__(self):
        if self.timestamp is None:
            from datetime import UTC, datetime

            self.timestamp = datetime.now(UTC).isoformat()

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str):
        return cls(**json.loads(data))


@dataclass
class BatchCompletedEvent(BaseCampaignEvent):
    """Event sent when a batch processing completes."""

    type: str = CampaignEventType.BATCH_COMPLETED
    processed_count: int = 0
    failed_count: int = 0
    batch_size: int = 0
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        super().__post_init__()
        if self.metadata is None:
            self.metadata = {}


@dataclass
class BatchFailedEvent(BaseCampaignEvent):
    """Event sent when a batch processing fails."""

    type: str = CampaignEventType.BATCH_FAILED
    error: str = ""
    processed_count: int = 0
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        super().__post_init__()
        if self.metadata is None:
            self.metadata = {}


@dataclass
class SyncStartedEvent(BaseCampaignEvent):
    """Event sent when campaign source sync starts."""

    type: str = CampaignEventType.SYNC_STARTED
    source_type: str = ""
    source_id: str = ""


@dataclass
class SyncCompletedEvent(BaseCampaignEvent):
    """Event sent when campaign source sync completes."""

    type: str = CampaignEventType.SYNC_COMPLETED
    total_rows: int = 0
    source_type: str = ""
    source_id: str = ""
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        super().__post_init__()
        if self.metadata is None:
            self.metadata = {}


@dataclass
class SyncFailedEvent(BaseCampaignEvent):
    """Event sent when campaign source sync fails."""

    type: str = CampaignEventType.SYNC_FAILED
    error: str = ""
    source_type: str = ""
    source_id: str = ""


@dataclass
class CampaignStartedEvent(BaseCampaignEvent):
    """Event sent when a campaign starts."""

    type: str = CampaignEventType.CAMPAIGN_STARTED
    workflow_id: int = 0
    total_rows: Optional[int] = None


@dataclass
class CampaignPausedEvent(BaseCampaignEvent):
    """Event sent when a campaign is paused."""

    type: str = CampaignEventType.CAMPAIGN_PAUSED
    processed_rows: int = 0
    failed_rows: int = 0


@dataclass
class CampaignResumedEvent(BaseCampaignEvent):
    """Event sent when a campaign is resumed."""

    type: str = CampaignEventType.CAMPAIGN_RESUMED
    processed_rows: int = 0
    failed_rows: int = 0


@dataclass
class CampaignCompletedEvent(BaseCampaignEvent):
    """Event sent when a campaign completes."""

    type: str = CampaignEventType.CAMPAIGN_COMPLETED
    total_rows: int = 0
    processed_rows: int = 0
    failed_rows: int = 0
    duration_seconds: Optional[float] = None


@dataclass
class CampaignFailedEvent(BaseCampaignEvent):
    """Event sent when a campaign fails."""

    type: str = CampaignEventType.CAMPAIGN_FAILED
    error: str = ""
    processed_rows: int = 0
    failed_rows: int = 0


@dataclass
class RetryNeededEvent(BaseCampaignEvent):
    """Event sent when a call needs retry."""

    type: str = CampaignEventType.RETRY_NEEDED
    workflow_run_id: int = 0
    queued_run_id: int = 0
    reason: str = ""  # RetryReason value
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        super().__post_init__()
        if self.metadata is None:
            self.metadata = {}


@dataclass
class RetryScheduledEvent(BaseCampaignEvent):
    """Event sent when a retry is scheduled."""

    type: str = CampaignEventType.RETRY_SCHEDULED
    queued_run_id: int = 0
    retry_run_id: int = 0
    retry_count: int = 0
    scheduled_for: str = ""  # ISO timestamp
    reason: str = ""  # RetryReason value


@dataclass
class RetryFailedEvent(BaseCampaignEvent):
    """Event sent when max retries reached."""

    type: str = CampaignEventType.RETRY_FAILED
    queued_run_id: int = 0
    retry_count: int = 0
    last_reason: str = ""  # RetryReason value


@dataclass
class CircuitBreakerTrippedEvent(BaseCampaignEvent):
    """Event sent when the circuit breaker trips and pauses a campaign."""

    type: str = CampaignEventType.CIRCUIT_BREAKER_TRIPPED
    failure_rate: float = 0.0
    failure_count: int = 0
    success_count: int = 0
    threshold: float = 0.0
    window_seconds: int = 0


def parse_campaign_event(data: str) -> Any:
    """Parse a campaign event message."""
    try:
        parsed = json.loads(data)
        event_type = parsed.get("type")

        # Map event types to their classes
        event_class_map = {
            CampaignEventType.BATCH_COMPLETED: BatchCompletedEvent,
            CampaignEventType.BATCH_FAILED: BatchFailedEvent,
            CampaignEventType.SYNC_STARTED: SyncStartedEvent,
            CampaignEventType.SYNC_COMPLETED: SyncCompletedEvent,
            CampaignEventType.SYNC_FAILED: SyncFailedEvent,
            CampaignEventType.CAMPAIGN_STARTED: CampaignStartedEvent,
            CampaignEventType.CAMPAIGN_PAUSED: CampaignPausedEvent,
            CampaignEventType.CAMPAIGN_RESUMED: CampaignResumedEvent,
            CampaignEventType.CAMPAIGN_COMPLETED: CampaignCompletedEvent,
            CampaignEventType.CAMPAIGN_FAILED: CampaignFailedEvent,
            CampaignEventType.RETRY_NEEDED: RetryNeededEvent,
            CampaignEventType.RETRY_SCHEDULED: RetryScheduledEvent,
            CampaignEventType.RETRY_FAILED: RetryFailedEvent,
            CampaignEventType.CIRCUIT_BREAKER_TRIPPED: CircuitBreakerTrippedEvent,
        }

        event_class = event_class_map.get(event_type)
        if event_class:
            return event_class(**parsed)

        # Unknown event type
        from loguru import logger

        logger.warning(f"Unknown campaign event type: {event_type}")
        return None

    except Exception as e:
        from loguru import logger

        logger.error(f"Failed to parse campaign event: {e}, data: {data}")
        return None
