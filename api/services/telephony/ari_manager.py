"""Standalone ARI Manager Service for distributed architecture.

This service maintains the single WebSocket connection to Asterisk ARI
and distributes events to multiple FastAPI workers via Redis pub/sub.

ARIManager creates an instance of ARIClientSupervisor and registers the callbacks
on_channel_start and on_channel_end. It is responsible to take in caller_channel
and setup ARIManagerConnection, i.e create bridge for externalMedia.

"""

import asyncio
import json
import os
import signal
import time
from typing import Dict, Optional

from api.constants import ENABLE_ARI_STASIS, REDIS_URL

# --- Add logging setup before importing loguru ---
from api.logging_config import setup_logging
from api.services.telephony.stasis_event_protocol import (
    BaseWorkerToARIManagerCommand,
    DisconnectCommand,
    RedisChannels,
    RedisKeys,
    SocketClosedCommand,
    StasisEndEvent,
    StasisStartEvent,
    TransferCommand,
    parse_command,
)

setup_logging()

import redis.asyncio as aioredis
import redis.exceptions
from loguru import logger

from api.services.telephony.ari_client import Channel
from api.services.telephony.ari_client_manager import (
    ARIClientManager,
    setup_ari_client_supervisor,
)
from api.services.telephony.ari_manager_connection import ARIManagerConnection
from pipecat.utils.enums import EndTaskReason


class ARIManager:
    """Manages ARI connection and distributes events to workers via Redis."""

    def __init__(self, redis_client: aioredis.Redis):
        self.redis = redis_client
        self.stasis_manager: Optional[ARIClientManager] = None
        self._running = False
        self._ari_client_supervisor = None
        self._tasks: Dict[str, asyncio.Task] = {}
        self._pubsubs: Dict[
            str, aioredis.client.PubSub
        ] = {}  # Track pubsub connections
        self._active_channels: set[str] = (
            set()
        )  # Track channels managed by this instance
        self._port_range = range(4000, 5000, 2)  # Even ports only
        self._channel_connections: Dict[
            str, ARIManagerConnection
        ] = {}  # Track connections by channel ID
        self._channel_disposed: Dict[str, bool] = {}  # Track channel disposed state
        self._socket_closed: Dict[str, bool] = {}  # Track socket closed state
        self._active_workers: list[str] = []  # Cached list of active workers
        self._worker_discovery_task: Optional[asyncio.Task] = None
        self._channel_to_worker: Dict[str, str] = {}  # Map channel to worker

    async def on_channel_start(self, caller_channel: Channel, call_context_vars: dict):
        """Handle new channel from ARIClientManager with atomically allocated port."""
        try:
            # Atomically allocate port for this channel (prevents race conditions)
            port = await self._get_and_allocate_port_atomic(caller_channel.id)

            # Create connection with allocated port
            connection = ARIManagerConnection(
                caller_channel=caller_channel,
                host=os.getenv("ARI_STASIS_APP_ENDPOINT"),
                port=port,
            )

            # Track the connection
            self._channel_connections[caller_channel.id] = connection
            # Initialize channel state flags
            self._channel_disposed[caller_channel.id] = False
            self._socket_closed[caller_channel.id] = False

            # Handle the connection
            await self._on_stasis_call(connection, call_context_vars)

        except Exception as e:
            logger.exception(f"Error handling new channel {caller_channel.id}: {e}")
            # Release port if allocation failed
            await self._release_port_for_channel(caller_channel.id)

    async def on_channel_end(self, channel_id: str):
        """Handle channel end notification from ARIClientManager."""
        logger.info(f"channelID: {channel_id} Received channel end notification")

        # Find the connection for this channel
        connection = None
        caller_channel_id = None

        # Check if it's a caller channel
        if channel_id in self._channel_connections:
            connection = self._channel_connections[channel_id]
            caller_channel_id = channel_id
        else:
            # TODO: We are currently not handling StasisEnd on ExternalMedia
            for conn_channel_id, conn in self._channel_connections.items():
                if conn.em_channel_id and conn.em_channel_id == channel_id:
                    logger.debug(
                        f"channelID: {channel_id} ExternalMedia StasisEnd - Ignoring"
                    )
                    # connection = conn
                    # caller_channel_id = conn_channel_id
                    break

        # Publish StasisEnd event to worker immediately
        if connection and caller_channel_id:
            worker_id = self._get_worker_for_channel(caller_channel_id)
            event = StasisEndEvent(
                channel_id=caller_channel_id,
                reason=EndTaskReason.USER_HANGUP.value,
            )
            await self.redis.publish(
                RedisChannels.worker_events(worker_id), event.to_json()
            )
            logger.info(f"channelID: {channel_id} Published StasisEnd event")

            # Notify the connection about channel end
            await connection.notify_channel_end()

            # Mark channel as disposed
            if caller_channel_id in self._channel_disposed:
                self._channel_disposed[caller_channel_id] = True
                # Check if both flags are set to cleanup
                await self._check_and_cleanup_channel(caller_channel_id)

    async def _on_stasis_call(
        self, connection: ARIManagerConnection, call_context_vars: dict
    ):
        """Handle new Stasis call by setting up the connection and publishing to Redis."""
        try:
            # Setup the connection (create bridge and external media)
            await connection.setup_call()

            if not connection.is_connected():
                logger.warning("Connection is not connected, skipping")
                return

            # Extract all necessary information after bridge is created
            channel_id = connection.caller_channel_id
            em_channel_id = connection.em_channel_id
            bridge_id = connection.bridge_id

            # Track this channel as active
            self._active_channels.add(channel_id)

            # Create event with all connection details
            event = StasisStartEvent(
                channel_id=channel_id,
                caller_channel_id=channel_id,
                em_channel_id=em_channel_id,
                bridge_id=bridge_id,
                local_addr=list(connection.local_addr),
                remote_addr=list(connection.remote_addr)
                if connection.remote_addr
                else None,
                call_context_vars=call_context_vars,
            )

            # Select worker using round-robin
            worker_id = await self._select_worker()
            if worker_id is None:
                logger.error(f"channelID: {channel_id} No active workers available")
                await connection.disconnect()
                return

            # Track channel to worker mapping
            self._channel_to_worker[channel_id] = worker_id
            channel = RedisChannels.worker_events(worker_id)

            # Publish event to specific worker
            await self.redis.publish(channel, event.to_json())
            logger.info(
                f"channelID: {channel_id} Published stasis_start event to worker {worker_id}"
            )

            # Start monitoring for commands from workers
            self._tasks[channel_id] = asyncio.create_task(
                self._monitor_channel_commands(channel_id, connection)
            )

        except Exception as e:
            logger.exception(f"Error handling stasis call: {e}")

    async def _get_and_allocate_port_atomic(self, channel_id: str) -> int:
        """Atomically find and allocate an available port using Redis Lua script.

        This method prevents race conditions by using a Lua script that executes
        atomically in Redis, ensuring that two concurrent calls cannot allocate
        the same port.
        """
        # Lua script for atomic port allocation
        lua_script = """
        local port_range_start = tonumber(ARGV[1])
        local port_range_end = tonumber(ARGV[2])
        local port_range_step = tonumber(ARGV[3])
        local channel_id = KEYS[1]
        local timestamp = ARGV[4]
        
        -- Check if channel already has a port allocated
        local existing_port = redis.call('HGET', 'channel_ports', channel_id)
        if existing_port then
            return tonumber(existing_port)
        end
        
        -- Find first available port
        for port = port_range_start, port_range_end, port_range_step do
            local port_str = tostring(port)
            local exists = redis.call('HEXISTS', 'port_channels', port_str)
            if exists == 0 then
                -- Atomically allocate the port
                redis.call('HSET', 'channel_ports', channel_id, port)
                redis.call('HSET', 'port_channels', port_str, channel_id)
                redis.call('HSET', 'channel_allocation_time', channel_id, timestamp)
                return port
            end
        end
        
        return -1  -- No ports available
        """

        # Execute the Lua script with port range parameters
        port_start = min(self._port_range)
        port_end = max(self._port_range)
        port_step = self._port_range.step
        timestamp = int(time.time())

        port = await self.redis.eval(
            lua_script,
            1,  # Number of keys
            channel_id,  # KEYS[1]
            port_start,  # ARGV[1]
            port_end,  # ARGV[2]
            port_step,  # ARGV[3]
            timestamp,  # ARGV[4]
        )

        if port == -1:
            # If all ports exhausted, clean up orphaned ports and retry
            await self._cleanup_orphaned_ports()

            # Retry after cleanup
            port = await self.redis.eval(
                lua_script, 1, channel_id, port_start, port_end, port_step, timestamp
            )

            if port == -1:
                raise RuntimeError(
                    "No available ports in configured range after cleanup"
                )

        logger.debug(f"Atomically allocated port {port} for channel {channel_id}")
        return port

    async def _release_port_for_channel(self, channel_id: str):
        """Atomically release port when channel ends.

        Uses a Lua script to ensure all cleanup operations happen atomically,
        preventing partial cleanup or race conditions during release.
        """
        lua_script = """
        local channel_id = KEYS[1]
        
        -- Get the port allocated to this channel
        local port = redis.call('HGET', 'channel_ports', channel_id)
        
        if port then
            -- Atomically clean up all related entries
            redis.call('HDEL', 'channel_ports', channel_id)
            redis.call('HDEL', 'port_channels', port)
            redis.call('HDEL', 'channel_allocation_time', channel_id)
            return port
        end
        
        return nil
        """

        port = await self.redis.eval(lua_script, 1, channel_id)

        if port:
            logger.debug(f"Atomically released port {port} for channel {channel_id}")
        else:
            logger.debug(f"No port was allocated for channel {channel_id}")

    async def _discover_workers(self):
        """Periodically discover active workers from Redis."""
        try:
            while self._running:
                try:
                    # Get all worker IDs from the set
                    worker_ids = await self.redis.smembers(RedisKeys.workers_set())

                    # Filter to only active workers
                    active_workers = []
                    for worker_id in worker_ids:
                        worker_id = (
                            worker_id.decode()
                            if isinstance(worker_id, bytes)
                            else worker_id
                        )
                        worker_key = RedisKeys.worker_active(worker_id)
                        worker_data = await self.redis.get(worker_key)

                        if worker_data:
                            try:
                                data = json.loads(worker_data)
                                # Only include workers that are ready (not draining)
                                if data.get("status") == "ready":
                                    active_workers.append(worker_id)
                            except json.JSONDecodeError:
                                logger.warning(f"Invalid worker data for {worker_id}")

                    # Update the cached list atomically
                    self._active_workers = active_workers
                    logger.info(f"Discovered {len(active_workers)} active workers")

                except Exception as e:
                    logger.error(f"Error discovering workers: {e}")

                # Check every 5 seconds
                await asyncio.sleep(5)

        except asyncio.CancelledError:
            logger.debug("Worker discovery task cancelled")

    async def _select_worker(self) -> Optional[str]:
        """Select a worker using round-robin."""
        if not self._active_workers:
            return None

        # Use Redis to maintain round-robin index across restarts
        try:
            index = await self.redis.incr(RedisKeys.round_robin_index())
            worker_index = (index - 1) % len(self._active_workers)
            return self._active_workers[worker_index]
        except Exception as e:
            logger.error(f"Error selecting worker: {e}")
            # Fallback to first worker if Redis operation fails
            return self._active_workers[0] if self._active_workers else None

    def _get_worker_for_channel(self, channel_id: str) -> str:
        """Get the assigned worker for a channel (for sending commands)."""
        # Return the worker ID that was assigned to this channel
        return self._channel_to_worker.get(channel_id, "")

    async def _monitor_channel_commands(
        self, channel_id: str, connection: ARIManagerConnection
    ):
        """Listen for commands from workers for this channel."""
        # TODO: Not sure if its a good idea to monitor command for every channel
        # using pubsub. What happens if there are more number of calls than number
        # of tcp connections redis can support? We can do something similar to
        # Campaign Orchestrator, where we can subscribe to one channel and have
        # commands for every channel there.
        command_channel = RedisChannels.channel_commands(channel_id)
        pubsub = None

        try:
            pubsub = self.redis.pubsub()
            await pubsub.subscribe(command_channel)

            # Store the pubsub connection for cleanup
            self._pubsubs[channel_id] = pubsub

            logger.debug(f"channelID: {channel_id} Monitoring commands for channel")

            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        command = parse_command(message["data"])
                        if command:
                            await self._handle_worker_command(
                                channel_id, command, connection
                            )
                        else:
                            logger.warning(
                                f"Failed to parse command for {channel_id}: {message['data']}"
                            )
                    except Exception as e:
                        logger.exception(
                            f"Error handling command for {channel_id}: {e}"
                        )

        except asyncio.CancelledError:
            logger.debug(f"channelID: {channel_id} Command monitor cancelled")
            raise  # Re-raise to maintain proper cancellation semantics
        except (ConnectionError, redis.exceptions.ConnectionError) as e:
            # We close the pubsub before cancelling the task. So, the code
            # flow will arrive here
            pass
        except Exception as e:
            logger.exception(f"Error in command monitor for {channel_id}: {e}")

    async def _handle_worker_command(
        self,
        channel_id: str,
        command: BaseWorkerToARIManagerCommand,
        connection: ARIManagerConnection,
    ):
        """Execute commands from workers."""
        if isinstance(command, DisconnectCommand):
            logger.info(f"channelID: {channel_id} Worker requested disconnect")
            await connection.disconnect()

        elif isinstance(command, TransferCommand):
            logger.info(f"channelID: {channel_id} Worker requested transfer")
            await connection.transfer(command.context)

        elif isinstance(command, SocketClosedCommand):
            logger.info(f"channelID: {channel_id} Worker notified socket closed")

            # Mark socket as closed
            if channel_id in self._socket_closed:
                self._socket_closed[channel_id] = True

            # Release port immediately
            await self._release_port_for_channel(channel_id)

            # Check if both flags are set to cleanup
            await self._check_and_cleanup_channel(channel_id)
        else:
            logger.warning(
                f"channelID: {channel_id} Received unknown command: {command}"
            )

    async def _check_and_cleanup_channel(self, channel_id: str):
        """Check if both flags are set and cleanup channel if so."""
        channel_disposed = self._channel_disposed.get(channel_id, False)
        socket_closed = self._socket_closed.get(channel_id, False)

        logger.debug(
            f"channelID: {channel_id} Check cleanup - disposed: {channel_disposed}, socket_closed: {socket_closed}"
        )

        if channel_disposed and socket_closed:
            # Remove from active channels and connections
            self._active_channels.discard(channel_id)
            self._channel_connections.pop(channel_id, None)

            # Close pubsub connection first (before cancelling task)
            if channel_id in self._pubsubs:
                pubsub = self._pubsubs[channel_id]
                try:
                    command_channel = RedisChannels.channel_commands(channel_id)
                    await pubsub.unsubscribe(command_channel)
                    await pubsub.aclose()
                    logger.debug(
                        f"channelID: {channel_id} Closed pubsub connection in cleanup"
                    )
                except Exception as e:
                    logger.warning(f"Error closing pubsub for {channel_id}: {e}")
                finally:
                    del self._pubsubs[channel_id]

            # Cancel command monitor task
            if channel_id in self._tasks:
                task = self._tasks[channel_id]
                if not task.done():
                    # Task is still running, cancel it
                    task.cancel()
                    try:
                        # Wait for task to complete
                        await task
                        logger.debug(
                            f"channelID: {channel_id} Task completed after cancel"
                        )
                    except asyncio.CancelledError:
                        logger.debug(
                            f"channelID: {channel_id} Task cancelled successfully"
                        )
                    except Exception as e:
                        logger.warning(
                            f"channelID: {channel_id} Task raised exception: {e}"
                        )
                else:
                    # Task already completed
                    logger.debug(
                        f"channelID: {channel_id} Monitor task already completed"
                    )
                    try:
                        # Still await to get any exception that might have occurred
                        await task
                    except Exception as e:
                        logger.warning(
                            f"channelID: {channel_id} Completed task had exception: {e}"
                        )

                del self._tasks[channel_id]

            # Clean up the flag tracking
            self._channel_disposed.pop(channel_id, None)
            self._socket_closed.pop(channel_id, None)

            logger.info(f"channelID: {channel_id} Completed cleanup of all resources")

    async def _cleanup_orphaned_ports(self):
        """Clean up ports from previous ungraceful shutdowns."""
        try:
            # Get all channel-port mappings
            channel_ports = await self.redis.hgetall("channel_ports")
            if not channel_ports:
                return

            logger.info(
                f"Found {len(channel_ports)} existing port allocations, checking for orphans..."
            )

            cleaned = 0
            current_time = int(time.time())
            max_age_seconds = 3600  # 1 hour

            # On startup, we can safely assume any existing allocations are orphaned
            # since this is a fresh instance with no active channels yet
            if not self._active_channels:
                # Clean up all existing allocations on startup
                for channel_id, port in channel_ports.items():
                    allocation_time = await self.redis.hget(
                        "channel_allocation_time", channel_id
                    )
                    age_str = ""
                    if allocation_time:
                        age = current_time - int(allocation_time)
                        age_str = f" (aged {age}s)"

                    await self._release_port_for_channel(channel_id)
                    logger.info(
                        f"Cleaned up orphaned port {port} for channel {channel_id}{age_str}"
                    )
                    cleaned += 1
            else:
                # During runtime, only clean up channels not being tracked
                for channel_id, port in channel_ports.items():
                    if channel_id not in self._active_channels:
                        # Check allocation age
                        allocation_time = await self.redis.hget(
                            "channel_allocation_time", channel_id
                        )
                        if allocation_time:
                            age = current_time - int(allocation_time)
                            if age > max_age_seconds:
                                # Too old, clean up regardless
                                await self._release_port_for_channel(channel_id)
                                logger.info(
                                    f"Cleaned up stale port {port} for channel {channel_id} (aged {age}s)"
                                )
                                cleaned += 1
                                continue

                        # Not tracked by this instance, might be orphaned
                        # For safety, only clean up if reasonably old (5 minutes)
                        if (
                            allocation_time
                            and (current_time - int(allocation_time)) > 300
                        ):
                            await self._release_port_for_channel(channel_id)
                            logger.info(
                                f"Cleaned up orphaned port {port} for untracked channel {channel_id}"
                            )
                            cleaned += 1

            if cleaned > 0:
                logger.info(f"Cleaned up {cleaned} orphaned port allocations")

        except Exception as e:
            logger.exception(f"Error during orphaned port cleanup: {e}")

    async def _periodic_cleanup(self):
        """Periodically clean up orphaned ports."""
        cleanup_interval = 1800  # 30 minutes

        while self._running:
            try:
                await asyncio.sleep(cleanup_interval)
                if self._running:  # Check again after sleep
                    logger.info("Running periodic orphaned port cleanup...")
                    await self._cleanup_orphaned_ports()
            except asyncio.CancelledError:
                logger.debug("Periodic cleanup task cancelled")
                break
            except Exception as e:
                logger.exception(f"Error in periodic cleanup: {e}")

    async def run(self):
        """Main run loop for ARI Manager."""
        if not ENABLE_ARI_STASIS:
            logger.info("ARI Stasis integration disabled via environment variable")
            return

        # Setup ARI connection with supervisor
        self._running = True

        try:
            self._ari_client_supervisor = await setup_ari_client_supervisor(
                self.on_channel_start, self.on_channel_end
            )
            if not self._ari_client_supervisor:
                logger.error("Failed to setup ARI connection")
                return

            # Start worker discovery task
            self._worker_discovery_task = asyncio.create_task(self._discover_workers())

            # Wait a moment for initial worker discovery
            await asyncio.sleep(1)

            logger.info(
                f"ARI Manager started with {len(self._active_workers)} active workers"
            )

            # Clean up any orphaned ports from previous runs
            await self._cleanup_orphaned_ports()

            # Start periodic cleanup task
            cleanup_task = asyncio.create_task(self._periodic_cleanup())

            # Keep running until shutdown
            while self._running:
                await asyncio.sleep(1)

            logger.debug("ARIManager._running is false. Will cleanup and shutdown")

            # Cancel cleanup task
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass

        except Exception as e:
            logger.exception(f"ARI Manager error: {e}")
        finally:
            if self._ari_client_supervisor:
                await self._ari_client_supervisor.close()
            logger.info("ARI Manager stopped")

    async def shutdown(self):
        """Graceful shutdown."""
        logger.info("Shutting down ARI Manager...")

        # Close supervisor first to prevent reconnection attempts
        if self._ari_client_supervisor:
            await self._ari_client_supervisor.close()

        # Cancel worker discovery task
        if self._worker_discovery_task:
            self._worker_discovery_task.cancel()
            try:
                await self._worker_discovery_task
            except asyncio.CancelledError:
                pass
            self._worker_discovery_task = None

        # Now set running to False
        self._running = False

        # Clean up all active channel ports before shutting down
        if self._active_channels:
            logger.info(f"Cleaning up {len(self._active_channels)} active channels...")
            for channel_id in list(
                self._active_channels
            ):  # Copy to avoid modification during iteration
                await self._release_port_for_channel(channel_id)
                logger.info(
                    f"Released port for active channel {channel_id} during shutdown"
                )
            self._active_channels.clear()

        # Clear flag tracking
        self._channel_disposed.clear()
        self._socket_closed.clear()

        # Cancel all monitoring tasks
        for task in self._tasks.values():
            task.cancel()

        # Wait for tasks to complete
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)


async def main():
    """Main entry point for ARI Manager service."""
    # Setup Redis connection
    redis = await aioredis.from_url(REDIS_URL, decode_responses=True)

    # Create and run manager
    manager = ARIManager(redis)

    # Create a shutdown event for clean coordination
    shutdown_event = asyncio.Event()

    # Setup signal handlers
    loop = asyncio.get_event_loop()

    def signal_handler(signum):
        logger.info(f"Received shutdown signal {signum}")
        # Set the shutdown event which will trigger shutdown
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))

    # Run manager with shutdown monitoring
    manager_task = asyncio.create_task(manager.run())
    shutdown_task = asyncio.create_task(shutdown_event.wait())

    try:
        # Wait for either normal completion or shutdown signal
        done, pending = await asyncio.wait(
            [manager_task, shutdown_task], return_when=asyncio.FIRST_COMPLETED
        )

        # If shutdown was triggered, perform graceful shutdown
        if shutdown_task in done:
            await manager.shutdown()
            # Cancel the manager task if still running
            if manager_task in pending:
                manager_task.cancel()
                try:
                    await manager_task
                except asyncio.CancelledError:
                    pass
    finally:
        await redis.aclose()


if __name__ == "__main__":
    # Configure logging
    logger.add("logs/ari_manager.log", rotation="10 MB")
    asyncio.run(main())
