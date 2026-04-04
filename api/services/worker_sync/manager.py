"""Worker sync manager for cross-worker state propagation.

Each FastAPI worker both publishes and listens on a single Redis pub/sub
channel.  When shared state changes (e.g. Langfuse credentials), the worker
that handled the mutation broadcasts a lightweight event.  Every worker
(including the sender) receives it and runs the registered handler, which
re-reads authoritative state from the DB.
"""

import asyncio
from typing import Awaitable, Callable, Dict

import redis.asyncio as aioredis
from loguru import logger

from api.enums import RedisChannel
from api.services.worker_sync.protocol import WorkerSyncEvent

SyncHandler = Callable[[WorkerSyncEvent], Awaitable[None]]


class WorkerSyncManager:
    """Propagates state changes across FastAPI workers via Redis pub/sub."""

    def __init__(self, redis_url: str):
        self._redis_url = redis_url
        self._handlers: Dict[str, SyncHandler] = {}
        self._redis: aioredis.Redis | None = None
        self._pubsub: aioredis.client.PubSub | None = None
        self._listener_task: asyncio.Task | None = None

    def register(self, event_type: str, handler: SyncHandler):
        """Register a handler for an event type. Call before start()."""
        self._handlers[event_type] = handler
        logger.info(f"Worker sync handler registered: {event_type}")

    async def broadcast(self, event_type: str, action: str, org_id: str = ""):
        """Publish an event to all workers (including self)."""
        if not self._redis:
            logger.warning("WorkerSyncManager not started, skipping broadcast")
            return
        event = WorkerSyncEvent(event_type=event_type, action=action, org_id=org_id)
        await self._redis.publish(RedisChannel.WORKER_SYNC.value, event.to_json())
        logger.debug(f"Broadcast worker sync: {event_type}/{action} org={org_id}")

    async def start(self):
        """Open a dedicated Redis connection and start the background listener."""
        self._redis = await aioredis.from_url(self._redis_url, decode_responses=True)
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(RedisChannel.WORKER_SYNC.value)
        self._listener_task = asyncio.create_task(self._listen())
        logger.info("WorkerSyncManager started")

    async def stop(self):
        """Cancel the listener and close the Redis connection."""
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._pubsub:
            await self._pubsub.unsubscribe(RedisChannel.WORKER_SYNC.value)
            await self._pubsub.close()
        if self._redis:
            await self._redis.close()
        logger.info("WorkerSyncManager stopped")

    async def _listen(self):
        """Background loop: receive events and dispatch to handlers."""
        try:
            async for message in self._pubsub.listen():
                if message["type"] != "message":
                    continue
                event = WorkerSyncEvent.from_json(message["data"])
                if not event:
                    continue
                handler = self._handlers.get(event.event_type)
                if handler:
                    try:
                        await handler(event)
                    except Exception:
                        logger.exception(
                            f"Worker sync handler error: {event.event_type}"
                        )
                else:
                    logger.warning(
                        f"No handler for worker sync event: {event.event_type}"
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Worker sync listener crashed")


# Module-level singleton, initialized in app lifespan
_manager: WorkerSyncManager | None = None


def get_worker_sync_manager() -> WorkerSyncManager:
    """Get the active WorkerSyncManager instance.

    Raises RuntimeError if called before the manager is started (i.e. outside
    the FastAPI lifespan).
    """
    if _manager is None:
        raise RuntimeError("WorkerSyncManager not initialized")
    return _manager


def set_worker_sync_manager(manager: WorkerSyncManager):
    """Set the module-level singleton. Called from the app lifespan."""
    global _manager
    _manager = manager
