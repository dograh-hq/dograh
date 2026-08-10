"""Org-wide call concurrency control for every call type (telephony, WebRTC,
agent streams, campaigns).

``service.py`` is the facade routes and tasks go through; ``rate_limiter.py``
owns the Redis storage primitives (slot sets, per-second rate limiting) used by
the facade and focused domain limiters.
"""

from api.services.call_concurrency.service import (
    CallConcurrencyLimitError,
    CallConcurrencyService,
    CallConcurrencySlot,
    WorkflowRunSlotAlreadyBoundError,
    call_concurrency,
)

__all__ = [
    "CallConcurrencyLimitError",
    "CallConcurrencyService",
    "CallConcurrencySlot",
    "WorkflowRunSlotAlreadyBoundError",
    "call_concurrency",
]
