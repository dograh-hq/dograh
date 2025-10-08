"""
ARI Client Manager using the new Async ARI Client.
Drop-in replacement for the existing ari_client_manager.py.
"""

import asyncio
import json
import os
import random
import time
from typing import Awaitable, Callable, Optional

import httpx
from loguru import logger

from api.services.telephony.ari_client import AsyncARIClient, Channel
from api.services.telephony.ari_client_singleton import ari_client_singleton


class ARIClientManager:
    """Manages ARI client connection and event handling.

    This is a compatibility wrapper around AsyncARIClient.
    """

    def __init__(
        self,
        ari_client: AsyncARIClient,
        app_endpoint: str,
        _conn_ctx=None,  # Not used with AsyncARIClient
    ):
        """Initialize the ARI client manager.

        Parameters
        ----------
        ari_client: AsyncARIClient
            The connected ARI client.
        app_endpoint: str
            The app endpoint for external media.
        _conn_ctx:
            Not used, kept for compatibility.
        """
        self._ari_client = ari_client
        self._app_endpoint = app_endpoint
        self._conn_ctx = _conn_ctx  # Not used but kept for compatibility
        self._start_handlers = []
        self._end_handlers = []
        self._running = False
        self._handlers_registered = False  # Track if handlers are registered

    def register_start_handler(
        self, handler: Callable[[Channel, dict], Awaitable[None]]
    ):
        """Register a handler for StasisStart events."""
        logger.debug(
            f"Registering start handler. Current count: {len(self._start_handlers)}"
        )
        self._start_handlers.append(handler)
        logger.debug(f"After registration, handler count: {len(self._start_handlers)}")

    def register_end_handler(self, handler: Callable[[str], Awaitable[None]]):
        """Register a handler for StasisEnd events."""
        self._end_handlers.append(handler)

    async def update_client(self, new_client: AsyncARIClient, new_conn_ctx=None):
        """Update to a new client (for reconnection)."""
        logger.info("Updating ARI client for reconnection")
        self._ari_client = new_client
        self._conn_ctx = new_conn_ctx
        # Clear old event handlers from the client before re-registering
        # to prevent duplicate handler registrations
        if hasattr(new_client, "_event_handlers"):
            new_client._event_handlers.clear()
        # Re-register event handlers
        self._register_handlers()

    def _register_handlers(self):
        """Register event handlers with the client."""
        logger.debug(
            f"_register_handlers called. Start handlers count: {len(self._start_handlers)}, End handlers count: {len(self._end_handlers)}"
        )

        async def on_stasis_start(event):
            """Handle StasisStart events."""
            channel = event.get("channel")

            # Only handle PJSIP and SIP channels
            if channel and hasattr(channel, "name"):
                if not (
                    channel.name.startswith("PJSIP") or channel.name.startswith("SIP")
                ):
                    logger.debug(
                        f"Ignoring StasisStart for non-SIP channel: {channel.name}"
                    )
                    return

            # Log the event
            logger.info(
                f"StasisStart event for channel: {channel.id if channel else 'unknown'}"
            )

            # Extract call context variables
            call_context_vars = {}
            try:
                # Get channel variables
                var_result = await channel.getChannelVar(
                    variable="LOCAL_ARI_CALL_VARIABLES"
                )
                call_context_vars = json.loads(var_result.get("value", "{}"))

                # Try to get phone number and fetch additional data
                phone_number = call_context_vars.get("phone")
                ari_data_uri = os.getenv("ARI_DATA_FETCHING_URI")

                if phone_number and ari_data_uri:
                    try:
                        start_time = time.time()
                        fetch_url = f"{ari_data_uri}{phone_number}"

                        async with httpx.AsyncClient() as client:
                            response = await client.get(fetch_url, timeout=10.0)
                            response.raise_for_status()

                            # Parse the response - get the latest line if multiple lines
                            response_text = response.text.strip()
                            if response_text:
                                lines = response_text.split("\n")
                                latest_line = lines[-1].strip()

                                if latest_line:
                                    # Parse the pipe-delimited data
                                    fields = latest_line.split("|")
                                    field_names = [
                                        "status",
                                        "user",
                                        "vendor_lead_code",
                                        "source_id",
                                        "list_id",
                                        "gmt_offset_now",
                                        "phone_code",
                                        "phone_number",
                                        "title",
                                        "first_name",
                                        "middle_initial",
                                        "last_name",
                                        "address1",
                                        "address2",
                                        "address3",
                                        "city",
                                        "state",
                                        "province",
                                        "postal_code",
                                        "country_code",
                                        "gender",
                                        "date_of_birth",
                                        "alt_phone",
                                        "email",
                                        "security_phrase",
                                        "comments",
                                        "called_count",
                                        "last_local_call_time",
                                        "rank",
                                        "owner",
                                        "entry_list_id",
                                        "lead_id",
                                    ]

                                    # Map fields to call_context_vars
                                    for i, field_name in enumerate(field_names):
                                        try:
                                            call_context_vars[field_name] = fields[i]
                                        except IndexError:
                                            logger.error(
                                                f"channelID: {channel.id} IndexError while accessing fields {i}"
                                            )

                                    elapsed_time = time.time() - start_time
                                    logger.info(
                                        f"channelID: {channel.id} Successfully fetched user details for phone: {phone_number} in {elapsed_time:.3f} seconds"
                                    )

                    except Exception as e:
                        elapsed_time = time.time() - start_time
                        logger.error(
                            f"channelID: {channel.id} Failed to fetch user details from ARI_DATA_FETCHING_URI after {elapsed_time:.3f} seconds: {e}"
                        )

                logger.debug(
                    f"channelID: {channel.id} call context variables: {call_context_vars}"
                )

            except (
                KeyError,
                AttributeError,
                httpx.HTTPStatusError,
                json.JSONDecodeError,
            ) as e:
                logger.debug(f"could not find variable LOCAL_ARI_CALL_VARIABLES: {e}")

            # Call all registered handlers with call_context_vars
            logger.debug(
                f"Calling {len(self._start_handlers)} start handlers for channel {channel.id}"
            )
            for i, handler in enumerate(self._start_handlers):
                try:
                    logger.debug(
                        f"  Calling start handler {i + 1}/{len(self._start_handlers)}"
                    )
                    await handler(channel, call_context_vars)
                except Exception as e:
                    logger.error(f"Error in StasisStart handler {i + 1}: {e}")

        async def on_stasis_end(event):
            """Handle StasisEnd events."""
            channel = event.get("channel", {})
            channel_id = channel.id if hasattr(channel, "id") else channel.get("id", "")

            # # Only handle PJSIP and SIP channels
            # if channel:
            #     channel_name = channel.name if hasattr(channel, 'name') else channel.get("name", "")
            #     if channel_name and not (channel_name.startswith("PJSIP") or channel_name.startswith("SIP")):
            #         logger.debug(f"Ignoring StasisEnd for non-SIP channel: {channel_name}")
            #         return

            logger.info(f"StasisEnd event for channel: {channel_id}")

            # Call all registered handlers
            for handler in self._end_handlers:
                try:
                    await handler(channel_id)
                except Exception as e:
                    logger.error(f"Error in StasisEnd handler: {e}")

        # Register with the AsyncARIClient
        logger.debug(f"Registering StasisStart and StasisEnd with AsyncARIClient")
        self._ari_client.on_event("StasisStart", on_stasis_start)
        self._ari_client.on_event("StasisEnd", on_stasis_end)
        logger.debug(f"Event handlers registered with client")

    async def run(self):
        """Run the event loop.

        The actual WebSocket handling is done by AsyncARIClient.
        This just registers handlers and waits.
        """
        logger.debug("Running ARIClientManager")
        self._running = True
        # Register handlers only once, on first run
        if not self._handlers_registered:
            self._register_handlers()
            self._handlers_registered = True

        try:
            # The AsyncARIClient.run() method handles WebSocket
            # We don't call it here as it's called by the supervisor
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.debug(f"ARIClientManager run cancelled")
            self._running = False
            raise
        finally:
            self._running = False


class _ARIClientManagerSupervisor:
    """Supervisor that maintains ARI connection with automatic reconnection.

    This replaces the asyncari-based supervisor with AsyncARIClient.
    """

    # Reconnection parameters
    _INITIAL_BACKOFF = 1  # Start with 1 second
    _MAX_BACKOFF = 60  # Max 60 seconds between retries

    def __init__(
        self,
        on_channel_start: Callable[[Channel, dict], Awaitable[None]],
        on_channel_end: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        self._on_channel_start = on_channel_start
        self._on_channel_end = on_channel_end
        self._shutting_down = False

    async def start(self):
        """Start the supervisor and maintain connection."""
        await self._runner()

    async def stop(self):
        """Stop the supervisor."""
        logger.info("Stopping ARI Client Manager Supervisor")
        self._shutting_down = True

    async def __aenter__(self):
        """Async context manager entry."""
        asyncio.create_task(self.start())
        return self

    async def __aexit__(self, *args):
        """Async context manager exit."""
        await self.stop()

    async def _runner(self):
        """Main reconnection loop using AsyncARIClient."""
        backoff = self._INITIAL_BACKOFF
        ari_client_manager: Optional[ARIClientManager] = None

        while not self._shutting_down:
            client = None

            try:
                logger.debug("Going to connect with ARI")

                # Get configuration from environment
                base_url = os.getenv("ARI_STASIS_ENDPOINT")
                username = os.getenv("ARI_STASIS_USER")
                password = os.getenv("ARI_STASIS_USER_PASSWORD")
                app = os.getenv("ARI_STASIS_APP_NAME")

                # Convert HTTP to WebSocket URL
                ws_url = base_url.replace("http://", "ws://").replace(
                    "https://", "wss://"
                )

                # Create and connect the AsyncARIClient
                client = AsyncARIClient(ws_url, username, password, app)
                await client.connect()

                # Update the singleton with the new client
                ari_client_singleton.set_client(client)

                if ari_client_manager is None:
                    # First connection - create new manager
                    logger.debug("Creating new ARIClientManager (first connection)")
                    ari_client_manager = ARIClientManager(
                        client,
                        os.getenv("ARI_STASIS_APP_ENDPOINT"),
                        _conn_ctx=None,  # Not needed with AsyncARIClient
                    )
                    logger.debug(f"Registering handlers with new manager")
                    ari_client_manager.register_start_handler(self._on_channel_start)
                    if self._on_channel_end:
                        ari_client_manager.register_end_handler(self._on_channel_end)
                else:
                    # Reconnection - update existing manager
                    logger.debug("Updating existing ARIClientManager (reconnection)")
                    # Don't re-register start and end handlers as they're already registered
                    await ari_client_manager.update_client(client, None)

                logger.info("Connected to ARI — supervisor entering event loop")

                # Reset backoff after successful connection
                backoff = self._INITIAL_BACKOFF

                # Create tasks for both the client and manager
                client_task = asyncio.create_task(client.run())
                manager_task = asyncio.create_task(ari_client_manager.run())

                # Wait for either to complete (likely due to disconnection)
                done, pending = await asyncio.wait(
                    {client_task, manager_task}, return_when=asyncio.FIRST_COMPLETED
                )

                # Cancel the other task
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            except asyncio.CancelledError:
                # Check if we're shutting down
                if self._shutting_down or asyncio.current_task().cancelled():
                    logger.debug("ARI supervisor task cancelled — shutting down")
                    break

                # Otherwise it's a transient connection error
                logger.warning("ARI connection lost due to CancelledError — will retry")

                # Force a context switch to reset event loop state
                await asyncio.sleep(0)

            except Exception as exc:
                # Check if we're shutting down
                if self._shutting_down or asyncio.current_task().cancelled():
                    logger.warning("Exiting due to shutdown during exception handling")
                    break

                # Log and retry
                logger.warning(f"ARI connection failed or lost: {exc!r} - will retry")

            finally:
                # Disconnect client if connected
                if client:
                    try:
                        await client.disconnect()
                    except Exception as e:
                        logger.warning(f"Error disconnecting client: {e}")
                    # Clear the singleton when disconnecting
                    ari_client_singleton.clear()

            # Check if we're shutting down before sleeping
            if self._shutting_down:
                logger.debug("Exiting reconnection loop due to shutdown")
                break

            # Exponential back-off with jitter before the next attempt
            jitter = random.uniform(0.1, backoff)
            logger.debug(f"Waiting {jitter:.1f} seconds before reconnecting...")

            # Sleep with proper event loop handling
            await asyncio.sleep(0)  # Yield control first
            await asyncio.sleep(jitter)

            logger.debug(f"Finished sleeping for {jitter} seconds")
            backoff = min(backoff * 2, self._MAX_BACKOFF)
            logger.debug(f"New backoff value: {backoff}, continuing loop...")


async def setup_ari_client_supervisor(
    on_channel_start: Callable[[Channel, dict], Awaitable[None]],
    on_channel_end: Callable[[str], Awaitable[None]] | None = None,
) -> "_ARIClientManagerSupervisor | None":
    """Start a background supervisor that keeps the ARI connection alive.

    This is a drop-in replacement for the asyncari-based function.
    Uses AsyncARIClient instead of asyncari.
    """
    logger.info("Starting ARI Client Supervisor with AsyncARIClient")

    supervisor = _ARIClientManagerSupervisor(on_channel_start, on_channel_end)

    # Start the supervisor in the background
    asyncio.create_task(supervisor.start())

    return supervisor
