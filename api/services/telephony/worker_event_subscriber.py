"""Worker Event Subscriber for distributed ARI architecture.

This component runs in each FastAPI worker process and subscribes to
Redis events from the ARI Manager. It creates pipelines for assigned calls
without any direct ARI connection.
"""

import asyncio
import json
import uuid
from typing import Awaitable, Callable, Optional

import redis.asyncio as aioredis
from loguru import logger

from api.routes.stasis_rtp import on_stasis_call
from api.services.telephony.stasis_event_protocol import (
    DisconnectCommand,
    RedisChannels,
    RedisKeys,
    StasisEndEvent,
    StasisStartEvent,
    parse_event,
)
from api.services.telephony.stasis_rtp_connection import StasisRTPConnection
from pipecat.utils.context import set_current_run_id


class WorkerEventSubscriber:
    """Subscribes to ARI events from Redis and processes them in the worker."""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        on_stasis_call: Callable[[StasisRTPConnection, dict], Awaitable[None]],
    ):
        self.redis = redis_client
        self.worker_id = str(uuid.uuid4())  # Generate unique worker ID
        self.on_stasis_call = on_stasis_call
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._active_connections: dict[str, StasisRTPConnection] = {}
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._cleanup_tasks: dict[str, asyncio.Task] = {}
        self._shutting_down = False
        self._shutdown_event = asyncio.Event()

    async def start(self):
        """Start the event subscriber."""
        if self._task is None:
            self._running = True

            # Register worker in Redis
            await self._register_worker()

            # Start main event loop
            self._task = asyncio.create_task(
                self._run(), name=f"worker_subscriber_{self.worker_id}"
            )

            # Start heartbeat task
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(), name=f"worker_heartbeat_{self.worker_id}"
            )

            logger.info(f"Worker {self.worker_id} event subscriber started")

    async def _register_worker(self):
        """Register this worker in Redis."""
        worker_key = RedisKeys.worker_active(self.worker_id)
        worker_data = json.dumps({"status": "ready", "active_calls": 0})

        # Set with TTL of 30 seconds (will be refreshed by heartbeat)
        await self.redis.setex(worker_key, 30, worker_data)

        # Add to workers set
        await self.redis.sadd(RedisKeys.workers_set(), self.worker_id)

        logger.info(f"Worker {self.worker_id} registered in Redis")

    async def _heartbeat_loop(self):
        """Send periodic heartbeats to Redis."""
        try:
            while self._running:
                # Update worker status with current active call count
                worker_key = RedisKeys.worker_active(self.worker_id)
                worker_data = json.dumps(
                    {
                        "status": "draining" if self._shutting_down else "ready",
                        "active_calls": len(self._active_tasks),
                    }
                )

                # Refresh TTL to 30 seconds
                await self.redis.setex(worker_key, 30, worker_data)

                # Wait 10 seconds before next heartbeat
                await asyncio.sleep(10)

        except asyncio.CancelledError:
            logger.debug(f"Worker {self.worker_id} heartbeat cancelled")
        except Exception as e:
            logger.exception(f"Worker {self.worker_id} heartbeat error: {e}")

    async def graceful_shutdown(self, max_wait_seconds: int = 300):
        """Gracefully shutdown the worker, waiting for calls to complete.

        Args:
            max_wait_seconds: Maximum time to wait for calls to complete (default 5 minutes)
        """
        logger.info(f"Worker {self.worker_id} starting graceful shutdown")

        # Mark as shutting down to prevent new calls
        self._shutting_down = True

        # Update status in Redis to 'draining'
        worker_key = RedisKeys.worker_active(self.worker_id)
        worker_data = json.dumps(
            {"status": "draining", "active_calls": len(self._active_tasks)}
        )
        await self.redis.setex(worker_key, 30, worker_data)

        # Wait for active tasks to complete (with timeout)
        start_time = asyncio.get_event_loop().time()
        while (
            self._active_tasks
            and (asyncio.get_event_loop().time() - start_time) < max_wait_seconds
        ):
            active_count = len(self._active_tasks)
            logger.info(
                f"Worker {self.worker_id} waiting for {active_count} active calls to complete"
            )

            # Update Redis with current status
            worker_data = json.dumps(
                {"status": "draining", "active_calls": active_count}
            )
            await self.redis.setex(worker_key, 30, worker_data)

            # Wait a bit before checking again
            await asyncio.sleep(5)

        # Force stop if timeout reached
        if self._active_tasks:
            logger.warning(
                f"Worker {self.worker_id} forcefully stopping {len(self._active_tasks)} active calls after timeout channel_ids: {list(self._active_tasks.keys())}"
            )

        await self.stop()

    async def stop(self):
        """Stop the event subscriber and deregister from Redis."""
        self._running = False

        # Deregister from Redis
        await self._deregister_worker()

        # Cancel all active call processing tasks
        for channel_id, task in list(self._active_tasks.items()):
            if not task.done():
                logger.info(f"Cancelling active call task for channel {channel_id}")
                task.cancel()

        # Cancel all cleanup tasks
        for channel_id, task in list(self._cleanup_tasks.items()):
            if not task.done():
                logger.info(f"Cancelling cleanup task for channel {channel_id}")
                task.cancel()

        # Wait for all tasks to complete
        all_tasks = list(self._active_tasks.values()) + list(
            self._cleanup_tasks.values()
        )
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)

        # Cancel heartbeat task
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info(f"Worker {self.worker_id} event subscriber stopped")

    async def _deregister_worker(self):
        """Remove this worker from Redis."""
        try:
            # Remove from active workers
            await self.redis.delete(RedisKeys.worker_active(self.worker_id))

            # Remove from workers set
            await self.redis.srem(RedisKeys.workers_set(), self.worker_id)

            logger.info(f"Worker {self.worker_id} deregistered from Redis")
        except Exception as e:
            logger.error(f"Error deregistering worker {self.worker_id}: {e}")

    async def _run(self):
        """Main subscriber loop."""
        self._running = True
        channel = RedisChannels.worker_events(self.worker_id)
        pubsub = self.redis.pubsub()

        try:
            await pubsub.subscribe(channel)
            logger.info(f"Worker {self.worker_id} subscribed to {channel}")

            async for message in pubsub.listen():
                if not self._running:
                    break

                if message["type"] == "message":
                    try:
                        await self._handle_event(message["data"])
                    except Exception as e:
                        logger.exception(f"Error handling event: {e}")

        except asyncio.CancelledError:
            logger.debug(f"Worker {self.worker_id} subscriber cancelled")
        except Exception as e:
            logger.exception(f"Worker {self.worker_id} subscriber error: {e}")
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    async def _handle_event(self, data: str):
        """Handle an event from the ARI Manager."""
        event = parse_event(data)
        if not event:
            logger.warning(f"Failed to parse event: {data}")
            return

        if isinstance(event, StasisStartEvent):
            await self._handle_stasis_start(event)
        elif isinstance(event, StasisEndEvent):
            await self._handle_stasis_end(event)
        else:
            logger.warning(
                f"channelID: {event.channel_id} Unhandled event type: {type(event)}"
            )

    async def _handle_stasis_start(self, event: StasisStartEvent):
        """Handle a new call assignment."""

        channel_id = event.channel_id
        logger.info(
            f"channelID: {channel_id} Worker {self.worker_id} handling StasisStart"
        )

        try:
            # Create StasisRTPConnection without ARI client
            connection = StasisRTPConnection(
                redis_client=self.redis,
                channel_id=channel_id,
                caller_channel_id=event.caller_channel_id,
                em_channel_id=event.em_channel_id,
                bridge_id=event.bridge_id,
                local_addr=tuple(event.local_addr) if event.local_addr else None,
                remote_addr=tuple(event.remote_addr) if event.remote_addr else None,
            )

            # Store connection for cleanup
            self._active_connections[channel_id] = connection

            # Create a background task to handle the call
            task = asyncio.create_task(
                self._process_call(connection, event.call_context_vars, channel_id),
                name=f"call_handler_{channel_id}",
            )
            self._active_tasks[channel_id] = task

        except Exception as e:
            logger.exception(f"Error handling StasisStart for {channel_id}: {e}")
            # Send disconnect command if setup fails
            await self._send_disconnect(channel_id, "setup_failed")

    async def _process_call(
        self, connection: StasisRTPConnection, call_context_vars: dict, channel_id: str
    ):
        """Process a call in the background."""
        try:
            await self.on_stasis_call(connection, call_context_vars)
        except Exception as e:
            logger.exception(f"Error processing call for {channel_id}: {e}")
            # Send disconnect command if call processing fails
            await self._send_disconnect(channel_id, "processing_failed")
        finally:
            # Clean up task reference
            if channel_id in self._active_tasks:
                del self._active_tasks[channel_id]

    async def _process_cleanup(self, channel_id: str):
        """Process call cleanup in the background."""
        try:
            if channel_id in self._active_connections:
                connection: StasisRTPConnection = self._active_connections[channel_id]

                # We must wait for the connection's invocation
                # before sending in remote disconnect. Otherwise,
                # the event handlers won't be registered and we won't
                # be able to call on_client_disconnected to cancel the
                # pipeline
                while not connection._connect_invoked:
                    await asyncio.sleep(0.1)

                # Set the run_id context so that we can have it in logs
                if connection.workflow_run_id:
                    set_current_run_id(connection.workflow_run_id)

                await connection.handle_remote_disconnect()
                del self._active_connections[channel_id]
        except Exception as e:
            logger.exception(f"Error during cleanup for {channel_id}: {e}")
        finally:
            # Clean up task reference from cleanup tasks dictionary
            if channel_id in self._cleanup_tasks:
                del self._cleanup_tasks[channel_id]

    async def _handle_stasis_end(self, event: StasisEndEvent):
        """Handle call termination."""
        channel_id = event.channel_id
        logger.info(
            f"channelID: {channel_id} Worker {self.worker_id} handling StasisEnd"
        )

        # Create a background task to handle the cleanup
        if channel_id in self._active_connections:
            # Check if there's already a cleanup task for this channel
            if (
                channel_id not in self._cleanup_tasks
                or self._cleanup_tasks[channel_id].done()
            ):
                # Lets start a new task, since we need to poll for
                # connection to be invoked from the pipeline before
                # caling remote disconnect
                task = asyncio.create_task(
                    self._process_cleanup(channel_id),
                    name=f"cleanup_handler_{channel_id}",
                )
                self._cleanup_tasks[channel_id] = task
            else:
                logger.warning(
                    f"channelID: {channel_id} Cleanup skipped - cleanup task still running"
                )

    async def _send_disconnect(self, channel_id: str, reason: str):
        """Send disconnect command to ARI Manager."""

        command = DisconnectCommand(channel_id=channel_id, reason=reason)
        channel = RedisChannels.channel_commands(channel_id)
        await self.redis.publish(channel, command.to_json())


async def setup_worker_subscriber(
    redis_client: aioredis.Redis,
) -> WorkerEventSubscriber:
    """Setup the worker event subscriber with dynamic registration."""
    subscriber = WorkerEventSubscriber(redis_client, on_stasis_call)
    logger.info(f"Setting up worker event subscriber with ID {subscriber.worker_id}")
    await subscriber.start()
    return subscriber
