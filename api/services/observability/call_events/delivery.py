"""Bounded, tracked post-call delivery. No event payloads enter DB or Redis."""

import asyncio

from loguru import logger

from .configuration import (
    CallEventsSettings,
    load_settings,
    registration,
    same_destination,
)
from .events import CallEvent

_tasks: set[asyncio.Task] = set()
_pending_bytes = 0
_concurrency = asyncio.Semaphore(4)
_stopping = False
MAX_PENDING_BYTES = 32 * 1024 * 1024


def submit(
    organization_id: int,
    settings: CallEventsSettings,
    events: tuple[CallEvent, ...],
    size_bytes: int,
) -> bool:
    global _pending_bytes
    if not events:
        return True
    if (
        _stopping
        or len(_tasks) >= 128
        or _pending_bytes + size_bytes > MAX_PENDING_BYTES
    ):
        logger.warning(
            "Call event export queue full/closed; dropping {} events for org {}",
            len(events),
            organization_id,
        )
        return False
    _pending_bytes += size_bytes
    task = asyncio.create_task(
        _deliver(organization_id, settings, events), name="call-event-export"
    )
    _tasks.add(task)

    def completed(done):
        global _pending_bytes
        _pending_bytes -= size_bytes
        _tasks.discard(done)

    task.add_done_callback(completed)
    return True


async def _deliver(organization_id, settings, events):
    try:
        async with _concurrency:
            async with asyncio.timeout(60):
                remaining = events
                for attempt in range(3):
                    if attempt:
                        await asyncio.sleep(2**attempt)
                    # Fresh credentials and authorization on every attempt;
                    # target edits must never redirect an already captured call.
                    current = await load_settings(organization_id)
                    if not same_destination(settings, current):
                        logger.info(
                            "Call event destination changed/disabled; discarding {} events for org {}",
                            len(remaining),
                            organization_id,
                        )
                        return
                    spec = registration(current.sink_type)
                    sink = spec.create(spec.config_model.model_validate(current.config))
                    try:
                        result = await sink.export(remaining)
                    finally:
                        await sink.close()
                    logger.info(
                        "Call event export org={} accepted={} rejected={} retryable={}",
                        organization_id,
                        len(result.accepted),
                        len(result.rejected),
                        len(result.retryable),
                    )
                    remaining = tuple(
                        e for e in remaining if e.event_id in result.retryable
                    )
                    if not remaining:
                        return
                logger.warning(
                    "Call event export retries exhausted; dropping {} events for org {}",
                    len(remaining),
                    organization_id,
                )
    except asyncio.CancelledError:
        logger.warning("Call event export interrupted for org {}", organization_id)
        raise
    except Exception as exc:
        # Do not log credentials, remote response bodies or transcript content.
        logger.warning(
            "Call event export failed for org {} ({})",
            organization_id,
            type(exc).__name__,
        )


async def shutdown() -> None:
    global _stopping
    _stopping = True
    tasks = list(_tasks)
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=15)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def start() -> None:
    global _stopping
    _stopping = False
