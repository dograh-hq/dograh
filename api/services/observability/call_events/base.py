"""Small destination contract; pipeline capture never sees vendor payloads."""

from dataclasses import dataclass, field
from typing import Callable, Protocol

from pydantic import BaseModel

from .events import CallEvent


@dataclass(frozen=True)
class ExportResult:
    accepted: frozenset[str] = frozenset()
    retryable: frozenset[str] = frozenset()
    rejected: frozenset[str] = frozenset()


class CallEventSink(Protocol):
    async def validate_connection(self) -> None: ...

    async def export(self, events: tuple[CallEvent, ...]) -> ExportResult: ...

    async def close(self) -> None: ...


@dataclass(frozen=True)
class CallEventSinkRegistration:
    config_model: type[BaseModel]
    create: Callable[[BaseModel], CallEventSink]
    sensitive_fields: tuple[str, ...] = ()
    destination_fields: tuple[str, ...] = ()


@dataclass
class EventBuffer:
    """Bound memory during a call; never persist events in workflow logs."""

    max_events: int = 20_000
    max_bytes: int = 4 * 1024 * 1024
    events: list[CallEvent] = field(default_factory=list)
    dropped: int = 0
    size_bytes: int = 0
    sealed: bool = False

    def emit(self, event: CallEvent) -> None:
        import copy
        import json
        from dataclasses import asdict

        if self.sealed:
            return
        size = len(json.dumps(asdict(event), default=str).encode())
        if (
            len(self.events) >= self.max_events
            or self.size_bytes + size > self.max_bytes
        ):
            self.dropped += 1
            # Preserve the final summary, even if capture filled its budget.
            if event.event != "call_ended" or not self.events:
                return
            removed = self.events.pop()
            self.size_bytes -= len(json.dumps(asdict(removed), default=str).encode())
        self.events.append(copy.deepcopy(event))
        self.size_bytes += size

    def seal(self) -> tuple[CallEvent, ...]:
        self.sealed = True
        events = tuple(self.events)
        self.events.clear()
        return events
