"""ARI WebSocket Event Listener Manager.

Standalone process that:
1. Queries the database for all organizations with ARI telephony configuration
2. Creates WebSocket connections to each ARI instance
3. Handles reconnection logic with exponential backoff
4. Processes StasisStart/StasisEnd events
5. Periodically refreshes configuration to detect new/removed organizations
"""

from api.logging_config import setup_logging

setup_logging()
import asyncio
import json
import signal
import time
from typing import Dict, Optional, Set
from urllib.parse import urlparse

import aiohttp
import redis.asyncio as aioredis
import websockets
from loguru import logger

from api.constants import REDIS_URL
from api.db import db_client
from api.enums import CallType, OrganizationConfigurationKey, WorkflowRunMode
from api.services.quota_service import check_dograh_quota_by_user_id
from api.services.telephony.call_transfer_manager import get_call_transfer_manager
from api.services.telephony.transfer_event_protocol import (
    TransferEvent,
    TransferEventType,
)

# Redis key pattern and TTL for channel-to-run mapping
_CHANNEL_KEY_PREFIX = "ari:channel:"
_EXT_CHANNEL_KEY_PREFIX = "ari:ext_channel:"
_CHANNEL_KEY_TTL = 3600  # 1 hour safety expiry


class ARIConnection:
    """Manages a single ARI WebSocket connection for an organization."""

    def __init__(
        self,
        organization_id: int,
        ari_endpoint: str,
        app_name: str,
        app_password: str,
        ws_client_name: str = "",
        inbound_workflow_id: int = None,
    ):
        self.organization_id = organization_id
        self.ari_endpoint = ari_endpoint.rstrip("/")
        self.app_name = app_name
        self.app_password = app_password
        self.ws_client_name = ws_client_name
        self.inbound_workflow_id = inbound_workflow_id

        self._ws: Optional[websockets.ClientConnection] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._reconnect_delay = 1  # Start with 1 second
        self._max_reconnect_delay = 300  # Max 300 seconds
        self._ping_interval = 30  # Send ping every 30 seconds

        # Redis client for channel-to-run reverse mapping (lazy init)
        self._redis_client: Optional[aioredis.Redis] = None

        # Transfer manager for handling call transfers
        self._call_transfer_manager = None

    async def _get_redis(self) -> aioredis.Redis:
        """Get Redis client instance (lazy init)."""
        if not self._redis_client:
            self._redis_client = await aioredis.from_url(
                REDIS_URL, decode_responses=True
            )
        return self._redis_client

    async def _get_transfer_manager(self):
        """Get transfer manager instance."""
        if not self._call_transfer_manager:
            self._call_transfer_manager = await get_call_transfer_manager()
        return self._call_transfer_manager

    async def _set_channel_run(self, channel_id: str, workflow_run_id: str):
        """Store channel_id -> workflow_run_id mapping in Redis."""
        r = await self._get_redis()
        await r.set(
            f"{_CHANNEL_KEY_PREFIX}{channel_id}",
            workflow_run_id,
            ex=_CHANNEL_KEY_TTL,
        )

    async def _get_channel_run(self, channel_id: str) -> Optional[str]:
        """Look up workflow_run_id for a channel_id from Redis."""
        r = await self._get_redis()
        return await r.get(f"{_CHANNEL_KEY_PREFIX}{channel_id}")

    async def _delete_channel_run(self, *channel_ids: str):
        """Delete channel-to-run mapping(s) from Redis."""
        if not channel_ids:
            return
        r = await self._get_redis()
        keys = [f"{_CHANNEL_KEY_PREFIX}{cid}" for cid in channel_ids]
        await r.delete(*keys)

    async def _mark_ext_channel(self, channel_id: str):
        """Mark a channel as an external media channel we created."""
        r = await self._get_redis()
        await r.set(f"{_EXT_CHANNEL_KEY_PREFIX}{channel_id}", "1", ex=_CHANNEL_KEY_TTL)

    async def _is_ext_channel(self, channel_id: str) -> bool:
        """Check if a channel is an external media channel we created."""
        r = await self._get_redis()
        return await r.exists(f"{_EXT_CHANNEL_KEY_PREFIX}{channel_id}") > 0

    async def _delete_ext_channel(self, channel_id: str):
        """Remove the external media channel marker."""
        r = await self._get_redis()
        await r.delete(f"{_EXT_CHANNEL_KEY_PREFIX}{channel_id}")

    @property
    def ws_url(self) -> str:
        """Build the ARI WebSocket URL."""
        parsed = urlparse(self.ari_endpoint)
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        return (
            f"{ws_scheme}://{parsed.netloc}/ari/events"
            f"?api_key={self.app_name}:{self.app_password}"
            f"&app={self.app_name}"
            f"&subscribeAll=true"
        )

    @property
    def connection_key(self) -> str:
        """Unique key for this connection based on config."""
        return f"{self.organization_id}:{self.ari_endpoint}:{self.app_name}"

    async def start(self):
        """Start the WebSocket connection in a background task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._connection_loop())
        logger.info(
            f"[ARI org={self.organization_id}] Started connection to {self.ari_endpoint}"
        )

    async def stop(self):
        """Stop the WebSocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(
            f"[ARI org={self.organization_id}] Stopped connection to {self.ari_endpoint}"
        )

    async def _connection_loop(self):
        """Main connection loop with reconnection logic."""
        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self._running:
                    break
                logger.warning(
                    f"[ARI org={self.organization_id}] Connection error: {e}. "
                    f"Reconnecting in {self._reconnect_delay}s..."
                )
                await asyncio.sleep(self._reconnect_delay)
                # Exponential backoff
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay
                )

    async def _connect_and_listen(self):
        """Establish WebSocket connection and listen for events."""
        ws_url = self.ws_url
        logger.info(
            f"[ARI org={self.organization_id}] Connecting to {self.ari_endpoint}..."
        )

        async for ws in websockets.connect(
            ws_url,
            ping_interval=self._ping_interval,
            ping_timeout=10,
            close_timeout=5,
        ):
            try:
                self._ws = ws

                # Reset reconnect delay on successful connection
                self._reconnect_delay = 1

                logger.info(
                    f"[ARI org={self.organization_id}] WebSocket connected to {self.ari_endpoint}"
                )

                async for message in ws:
                    if not self._running:
                        return

                    if isinstance(message, str):
                        await self._handle_event(message)
                    else:
                        logger.debug(
                            f"[ARI org={self.organization_id}] Received binary message, ignoring"
                        )

            except websockets.ConnectionClosed as e:
                if not self._running:
                    return
                logger.warning(
                    f"[ARI org={self.organization_id}] WebSocket closed: "
                    f"code={e.code}, reason={e.reason}. Reconnecting..."
                )
                continue
            finally:
                self._ws = None

    async def _handle_event(self, raw_data: str):
        """Handle an ARI WebSocket event."""
        try:
            event = json.loads(raw_data)
        except json.JSONDecodeError:
            logger.warning(
                f"[ARI org={self.organization_id}] Invalid JSON: {raw_data[:200]}"
            )
            return

        event_type = event.get("type", "unknown")
        channel = event.get("channel", {})
        channel_id = channel.get("id", "unknown")
        channel_state = channel.get("state", "unknown")

        # Log all events for each channel
        logger.debug(
            f"[ARI EVENT org={self.organization_id}] {event_type}: channel={channel_id}, state={channel_state}"
        )

        if event_type == "StasisStart":
            # Skip external media channels we created — they fire
            # their own StasisStart but need no further handling.
            if await self._is_ext_channel(channel_id):
                logger.debug(
                    f"[ARI org={self.organization_id}] StasisStart for our "
                    f"externalMedia channel {channel_id}, ignoring"
                )
                return

            app_args = event.get("args", [])
            caller = channel.get("caller", {})
            logger.info(
                f"[ARI org={self.organization_id}] StasisStart: "
                f"channel={channel_id}, state={channel_state}, "
                f"caller={caller.get('number', 'unknown')}, "
                f"args={app_args}"
            )

            if channel_state == "Ring":
                # Inbound call — arrived from outside, not yet answered
                asyncio.create_task(
                    self._handle_inbound_stasis_start(channel_id, channel_state, event)
                )
            else:
                # Outbound call (state == "Up") — originated by us
                # Check if this is a transfer destination channel (app_args starts with "transfer")
                # Transfer destinations run externally - we only track status to publish transfer event, not run our pipeline
                transfer_id = self._get_transfer_id(app_args)
                if transfer_id:
                    logger.info(
                        f"[ARI org={self.organization_id}] Transfer destination answered: "
                        f"channel={channel_id}, transfer_id={transfer_id}"
                    )
                    asyncio.create_task(
                        self._handle_transfer_answered(transfer_id, channel_id)
                    )
                    return

                # Parse args to extract workflow context
                args_dict = {}
                for arg in app_args:
                    for pair in arg.split(","):
                        if "=" in pair:
                            key, value = pair.split("=", 1)
                            args_dict[key.strip()] = value.strip()

                workflow_run_id = args_dict.get("workflow_run_id")
                workflow_id = args_dict.get("workflow_id")
                user_id = args_dict.get("user_id")

                if not workflow_run_id or not workflow_id or not user_id:
                    logger.warning(
                        f"[ARI org={self.organization_id}] StasisStart missing required args: "
                        f"workflow_run_id={workflow_run_id}, workflow_id={workflow_id}, user_id={user_id}"
                    )
                    return

                # Start pipeline connection in background task
                asyncio.create_task(
                    self._handle_stasis_start(
                        channel_id, channel_state, workflow_run_id, workflow_id, user_id
                    )
                )

        elif event_type == "StasisEnd":
            logger.info(
                f"[ARI org={self.organization_id}] StasisEnd: channel={channel_id}"
            )

            workflow_run_id = await self._get_channel_run(channel_id)
            if workflow_run_id:
                asyncio.create_task(
                    self._handle_stasis_end(channel_id, workflow_run_id)
                )

        elif event_type == "ChannelStateChange":
            logger.debug(
                f"[ARI org={self.organization_id}] ChannelStateChange: "
                f"channel={channel_id}, state={channel_state}"
            )

        elif event_type == "ChannelDestroyed":
            cause = event.get("cause", 0)
            cause_txt = event.get("cause_txt", "unknown")
            tech_cause = event.get("tech_cause", "unknown")
            logger.info(
                f"[ARI org={self.organization_id}] ChannelDestroyed: "
                f"channel={channel_id}, cause={cause} ({cause_txt}), tech_cause = {tech_cause}"
            )

            # Check if this is a transfer destination that failed
            transfer_id = await self._get_transfer_id_for_channel(channel_id)
            if transfer_id:
                failure_message = self._map_hangup_cause_to_message(
                    cause, tech_cause, cause_txt
                )
                asyncio.create_task(
                    self._handle_transfer_failed(
                        transfer_id, channel_id, failure_message
                    )
                )

        elif event_type == "ChannelDtmfReceived":
            digit = event.get("digit", "")
            logger.debug(
                f"[ARI org={self.organization_id}] DTMF: "
                f"channel={channel_id}, digit={digit}"
            )

        else:
            logger.debug(
                f"[ARI org={self.organization_id}] Event: {event_type} "
                f"channel={channel_id}"
            )

    async def _ari_request(self, method: str, path: str, **kwargs) -> dict:
        """Make an ARI REST API request."""

        url = f"{self.ari_endpoint}/ari{path}"
        auth = aiohttp.BasicAuth(self.app_name, self.app_password)

        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, auth=auth, **kwargs) as response:
                response_text = await response.text()
                if response.status not in (200, 201, 204):
                    logger.error(
                        f"[ARI org={self.organization_id}] REST API error: "
                        f"{method} {path} -> {response.status}: {response_text}"
                    )
                    return {}
                if response_text:
                    return json.loads(response_text)
                return {}

    async def _answer_channel(self, channel_id: str) -> bool:
        """Answer an ARI channel."""
        await self._ari_request("POST", f"/channels/{channel_id}/answer")
        # answer returns 204 No Content on success, so empty dict is OK
        logger.info(f"[ARI org={self.organization_id}] Answered channel {channel_id}")
        return True

    async def _create_external_media(
        self,
        workflow_id: str,
        user_id: str,
        workflow_run_id: str,
    ) -> str:
        """Create an external media channel via chan_websocket.

        Uses ARI externalMedia with transport=websocket so Asterisk connects
        to our backend over WebSocket (via websocket_client.conf).
        Dynamic routing params are passed as URI query params via v() in transport_data.
        """
        # v() appends URI query params to the websocket_client.conf URL
        # e.g. wss://api.dograh.com/ws/ari?workflow_id=1&user_id=2&workflow_run_id=3
        transport_data = (
            f"v(workflow_id={workflow_id},"
            f"user_id={user_id},"
            f"workflow_run_id={workflow_run_id})"
        )

        result = await self._ari_request(
            "POST",
            "/channels/externalMedia",
            params={
                "app": self.app_name,
                "external_host": self.ws_client_name,
                "format": "ulaw",
                "transport": "websocket",
                "encapsulation": "none",
                "connection_type": "client",
                "direction": "both",
                "transport_data": transport_data,
            },
        )
        ext_channel_id = result.get("id", "")
        if ext_channel_id:
            await self._mark_ext_channel(ext_channel_id)
            logger.info(
                f"[ARI org={self.organization_id}] Created external media channel: {ext_channel_id}"
            )
        return ext_channel_id

    async def _create_bridge_and_add_channels(self, channel_ids: list) -> str:
        """Create a bridge and add channels to it."""
        # Create bridge
        bridge_result = await self._ari_request(
            "POST",
            "/bridges",
            params={"type": "mixing", "name": f"bridge-{channel_ids[0]}"},
        )
        bridge_id = bridge_result.get("id", "")
        if not bridge_id:
            logger.error(f"[ARI org={self.organization_id}] Failed to create bridge")
            return ""

        # Add channels to bridge
        await self._ari_request(
            "POST",
            f"/bridges/{bridge_id}/addChannel",
            params={"channel": ",".join(channel_ids)},
        )
        logger.info(
            f"[ARI org={self.organization_id}] Bridge {bridge_id} created with channels: {channel_ids}"
        )
        return bridge_id

    async def _handle_inbound_stasis_start(
        self, channel_id: str, channel_state: str, event: dict
    ):
        """Handle an inbound call (StasisStart with state=Ring).

        Validates quota, creates a workflow run, then delegates to the
        standard answer→externalMedia→bridge pipeline.
        """
        channel = event.get("channel", {})
        caller_number = channel.get("caller", {}).get("number", "unknown")
        called_number = channel.get("dialplan", {}).get("exten", "unknown")

        try:
            # 1. Check inbound_workflow_id is configured
            if not self.inbound_workflow_id:
                logger.warning(
                    f"[ARI org={self.organization_id}] Inbound call on channel {channel_id} "
                    f"but no inbound_workflow_id configured — hanging up"
                )
                await self._delete_channel(channel_id)
                return

            # 2. Load workflow to get user_id and verify organization
            workflow = await db_client.get_workflow(
                self.inbound_workflow_id, organization_id=self.organization_id
            )
            if not workflow:
                logger.warning(
                    f"[ARI org={self.organization_id}] Workflow {self.inbound_workflow_id} "
                    f"not found or doesn't belong to this organization — hanging up"
                )
                await self._delete_channel(channel_id)
                return

            user_id = workflow.user_id

            # 3. Check quota
            quota_result = await check_dograh_quota_by_user_id(user_id)
            if not quota_result.has_quota:
                logger.warning(
                    f"[ARI org={self.organization_id}] Quota exceeded for user {user_id} "
                    f"— hanging up inbound call {channel_id}"
                )
                await self._delete_channel(channel_id)
                return

            # 4. Create workflow run
            call_id = channel_id
            workflow_run = await db_client.create_workflow_run(
                name=f"ARI Inbound {caller_number}",
                workflow_id=self.inbound_workflow_id,
                mode=WorkflowRunMode.ARI.value,
                user_id=user_id,
                call_type=CallType.INBOUND,
                initial_context={
                    "caller_number": caller_number,
                    "called_number": called_number,
                    "direction": "inbound",
                    "provider": "ari",
                },
                gathered_context={
                    "call_id": call_id,
                },
            )

            logger.info(
                f"[ARI org={self.organization_id}] Created inbound workflow run "
                f"{workflow_run.id} for channel {channel_id} "
                f"(caller={caller_number}, called={called_number})"
            )

            # 5. Answer the inbound channel
            await self._answer_channel(channel_id)

            # 6. Delegate to the standard pipeline
            await self._handle_stasis_start(
                channel_id,
                channel_state,
                str(workflow_run.id),
                str(self.inbound_workflow_id),
                str(user_id),
            )
        except Exception as e:
            logger.error(
                f"[ARI org={self.organization_id}] Error handling inbound StasisStart "
                f"for channel {channel_id}: {e}"
            )
            try:
                await self._delete_channel(channel_id)
            except Exception:
                pass

    async def _handle_stasis_start(
        self,
        channel_id: str,
        channel_state: str,
        workflow_run_id: str,
        workflow_id: str,
        user_id: str,
    ):
        """Handle StasisStart by creating external media and bridging."""
        try:
            logger.info(
                f"[ARI org={self.organization_id}] Setting up external media for "
                f"channel {channel_id} via ws_client={self.ws_client_name}"
            )

            # 1. Track channel for StasisEnd cleanup (Redis)
            await self._set_channel_run(channel_id, workflow_run_id)

            # 2. Create external media channel via chan_websocket
            # Asterisk connects to our backend using websocket_client.conf config,
            # with routing params appended as URI query params via v()
            ext_channel_id = await self._create_external_media(
                workflow_id, user_id, workflow_run_id
            )
            if not ext_channel_id:
                logger.error(
                    f"[ARI org={self.organization_id}] Failed to create external media for {channel_id}"
                )
                return

            # 3. Track ext channel for StasisEnd cleanup (Redis)
            await self._set_channel_run(ext_channel_id, workflow_run_id)

            # 4. Bridge the call channel with the external media channel
            bridge_id = await self._create_bridge_and_add_channels(
                [channel_id, ext_channel_id]
            )
            if not bridge_id:
                logger.error(
                    f"[ARI org={self.organization_id}] Failed to bridge channels"
                )
                return

            # 5. Store ARI resource IDs in gathered_context for cleanup/debugging
            await db_client.update_workflow_run(
                run_id=int(workflow_run_id),
                gathered_context={
                    "ext_channel_id": ext_channel_id,
                    "bridge_id": bridge_id,
                },
            )
        except Exception as e:
            logger.error(
                f"[ARI org={self.organization_id}] Error handling StasisStart "
                f"for channel {channel_id}: {e}"
            )

    async def _handle_stasis_end(self, channel_id: str, workflow_run_id: str):
        """Full teardown of all ARI resources on any channel's StasisEnd.

        When either channel (call or ext) fires StasisEnd, we tear down
        the bridge and both channels — like endConferenceOnExit.
        """
        try:
            workflow_run = await db_client.get_workflow_run_by_id(int(workflow_run_id))
            if not workflow_run or not workflow_run.gathered_context:
                logger.warning(
                    f"[ARI org={self.organization_id}] StasisEnd: no gathered_context "
                    f"for workflow_run {workflow_run_id}"
                )
                # Still clean up the Redis key for the channel that ended
                await self._delete_channel_run(channel_id)
                return

            ctx = workflow_run.gathered_context
            call_id = ctx.get("call_id")
            ext_channel_id = ctx.get("ext_channel_id")
            bridge_id = ctx.get("bridge_id")
            transfer_state = ctx.get("transfer_state")

            # Check if this is transfer-protected external channe. Skip full teardown if transfer is in progress and this is the external media channel
            # During call transfer, we preserve the caller-destination bridge
            if (
                transfer_state == "in-progress"
                and channel_id == ext_channel_id
                and ext_channel_id is not None
            ):
                logger.info(
                    f"[ARI org={self.organization_id}] Transfer in progress - skipping full teardown "
                    f"for external channel {channel_id}, preserving bridge {bridge_id} and caller {call_id}"
                )

                # Update transfer state to complete
                ctx["transfer_state"] = "complete"
                await db_client.update_workflow_run(
                    run_id=int(workflow_run_id), gathered_context=ctx
                )

                # Clean up only Redis markers for external channel
                await self._delete_channel_run(channel_id)
                await self._delete_ext_channel(channel_id)

                logger.info(
                    f"[ARI org={self.organization_id}] Transfer cleanup complete - preserved caller {call_id} "
                    f"in bridge {bridge_id}"
                )
                return

            # Normal full teardown for non-transfer scenarios (transfer_state is None or not in-progress)
            # Delete the bridge first (removes channels from it)
            if bridge_id:
                await self._delete_bridge(bridge_id)

            # Destroy both channels, skipping the one that already ended
            for cid in (call_id, ext_channel_id):
                if cid and cid != channel_id:
                    await self._delete_channel(cid)

            # Clean up all Redis reverse-mapping keys
            keys_to_delete = [
                cid for cid in (call_id, ext_channel_id, channel_id) if cid
            ]
            if keys_to_delete:
                await self._delete_channel_run(*keys_to_delete)

            # Clean up the Redis marker for external channel
            await self._delete_ext_channel(ext_channel_id)

            logger.info(
                f"[ARI org={self.organization_id}] StasisEnd full teardown for "
                f"channel={channel_id}, call={call_id}, ext={ext_channel_id}, bridge={bridge_id}"
            )
        except Exception as e:
            logger.error(
                f"[ARI org={self.organization_id}] Error cleaning up StasisEnd "
                f"for channel {channel_id}: {e}"
            )

    async def _delete_bridge(self, bridge_id: str):
        """Delete an ARI bridge. Ignores 404 (already gone)."""

        url = f"{self.ari_endpoint}/ari/bridges/{bridge_id}"
        auth = aiohttp.BasicAuth(self.app_name, self.app_password)

        async with aiohttp.ClientSession() as session:
            async with session.delete(url, auth=auth) as response:
                if response.status in (200, 204):
                    logger.info(
                        f"[ARI org={self.organization_id}] Deleted bridge {bridge_id}"
                    )
                elif response.status == 404:
                    logger.debug(
                        f"[ARI org={self.organization_id}] Bridge {bridge_id} already gone"
                    )
                else:
                    text = await response.text()
                    logger.error(
                        f"[ARI org={self.organization_id}] Failed to delete bridge {bridge_id}: "
                        f"{response.status} {text}"
                    )

    # ======== CALL TRANSFER HELPER METHODS ========

    def _map_hangup_cause_to_message(
        self, cause: int, tech_cause: str, cause_txt: str
    ) -> str:
        """Map Asterisk cause codes to user-friendly transfer failure messages."""
        if cause == 17 and tech_cause == "486":  # User busy/declined
            return "The person declined the call or their line is busy."
        elif cause == 19 and tech_cause == "480":  # No answer
            return "The transfer call was not answered. The person may be busy or unavailable right now."
        elif cause == 21:  # Call rejected
            return "The transfer call failed to connect. There may be a network issue or the number is unavailable."
        else:
            return f"Transfer failed: {cause_txt}"

    def _get_transfer_id(self, app_args: list) -> Optional[str]:
        """Get transfer_id if this is a transfer channel, None otherwise.

        Args format: ['transfer', '{transfer_id}', '{conf_name}']
        """
        if len(app_args) > 1 and app_args[0] == "transfer":
            transfer_id = app_args[1]
            logger.debug(
                f"[ARI org={self.organization_id}] Detected transfer channel with transfer_id: {transfer_id}"
            )
            return transfer_id
        return None

    async def _get_transfer_id_for_channel(self, channel_id: str) -> Optional[str]:
        """Get transfer_id for a channel by checking Redis mapping."""
        try:
            r = await self._get_redis()
            transfer_id = await r.get(f"ari:transfer_channel:{channel_id}")
            logger.debug(
                f"[ARI Transfer] Looking up transfer_id for channel {channel_id}: {transfer_id}"
            )
            return transfer_id
        except Exception as e:
            logger.error(
                f"[ARI org={self.organization_id}] Error getting transfer ID for channel {channel_id}: {e}"
            )
            return None

    async def _handle_transfer_answered(
        self, transfer_id: str, destination_channel_id: str
    ):
        """Handle transfer destination channel answered - publish success event."""
        try:
            logger.info(
                f"[ARI Transfer org={self.organization_id}] Destination {destination_channel_id} "
                f"answered for transfer {transfer_id}"
            )

            # Store channel mapping for potential future events and get transfer context
            transfer_manager = await self._get_transfer_manager()
            await transfer_manager.store_transfer_channel_mapping(
                destination_channel_id, transfer_id
            )
            context = await transfer_manager.get_transfer_context(transfer_id)
            if not context:
                logger.error(
                    f"[ARI Transfer org={self.organization_id}] No transfer context found for {transfer_id}"
                )
                return

            logger.info(
                f"[ARI Transfer org={self.organization_id}] Transfer {transfer_id} success: "
                f"caller={context.original_call_sid} -> destination={destination_channel_id}"
            )

            # Publish transfer success event - this will trigger the bridge swap in serializer
            success_event = TransferEvent(
                type=TransferEventType.TRANSFER_ANSWERED,
                transfer_id=transfer_id,
                original_call_sid=context.original_call_sid,
                transfer_call_sid=destination_channel_id,
                conference_name=context.conference_name,
                message="Transfer destination answered",
                status="success",
                action="transfer_success",
                end_call=True,
                timestamp=time.time(),
            )
            await transfer_manager.publish_transfer_event(success_event)

        except Exception as e:
            logger.error(
                f"[ARI Transfer org={self.organization_id}] Error handling transfer answer: {e}"
            )
            # On error, publish failure event
            await self._handle_transfer_failed(
                transfer_id, destination_channel_id, f"Transfer processing error: {e}"
            )

    async def _handle_transfer_failed(
        self, transfer_id: str, channel_id: str, reason: str
    ):
        """Handle transfer failure - publish failure event."""
        try:
            logger.info(f"[ARI Transfer] Transfer {transfer_id} failed: {reason}")

            # Get transfer context
            transfer_manager = await self._get_transfer_manager()
            context = await transfer_manager.get_transfer_context(transfer_id)

            # Publish failure event
            failure_event = TransferEvent(
                type=TransferEventType.TRANSFER_FAILED,
                transfer_id=transfer_id,
                original_call_sid=context.original_call_sid if context else "",
                transfer_call_sid=channel_id,
                message=f"Transfer failed: {reason}",
                status="failed",
                action="transfer_failed",
                reason=reason,
                end_call=False,
                timestamp=time.time(),
            )
            await transfer_manager.publish_transfer_event(failure_event)

        except Exception as e:
            logger.error(f"[ARI Transfer] Error handling transfer failure: {e}")

    async def _delete_channel(self, channel_id: str):
        """Delete (hang up) an ARI channel. Ignores 404 (already gone)."""

        url = f"{self.ari_endpoint}/ari/channels/{channel_id}"
        auth = aiohttp.BasicAuth(self.app_name, self.app_password)

        async with aiohttp.ClientSession() as session:
            async with session.delete(url, auth=auth) as response:
                if response.status in (200, 204):
                    logger.info(
                        f"[ARI org={self.organization_id}] Deleted channel {channel_id}"
                    )
                elif response.status == 404:
                    logger.debug(
                        f"[ARI org={self.organization_id}] Channel {channel_id} already gone"
                    )
                else:
                    text = await response.text()
                    logger.error(
                        f"[ARI org={self.organization_id}] Failed to delete channel {channel_id}: "
                        f"{response.status} {text}"
                    )


class ARIManager:
    """Manages ARI WebSocket connections for all organizations."""

    def __init__(self):
        self._connections: Dict[str, ARIConnection] = {}  # key -> connection
        self._running = False
        self._config_refresh_interval = 60  # Check for config changes every 60 seconds

    async def start(self):
        """Start the ARI manager."""
        self._running = True
        logger.info("ARI Manager starting...")

        # Initial load of configurations
        await self._refresh_connections()

        # Start periodic config refresh
        while self._running:
            await asyncio.sleep(self._config_refresh_interval)
            if self._running:
                await self._refresh_connections()

    async def stop(self):
        """Stop all connections and clean up."""
        self._running = False
        logger.info("ARI Manager stopping...")

        # Stop all connections
        for conn in self._connections.values():
            await conn.stop()
        self._connections.clear()
        logger.info("ARI Manager stopped")

    async def _refresh_connections(self):
        """
        Refresh connections based on current database configurations.

        - Starts new connections for new ARI configurations
        - Stops connections for removed configurations
        - Restarts connections if configuration changed
        """
        try:
            active_configs = await self._load_ari_configs()
        except Exception as e:
            logger.error(f"Failed to load ARI configurations: {e}")
            return

        active_keys: Set[str] = set()

        for config in active_configs:
            org_id = config["organization_id"]
            ari_endpoint = config["ari_endpoint"]
            app_name = config["app_name"]
            app_password = config["app_password"]
            ws_client_name = config["ws_client_name"]
            inbound_workflow_id = config.get("inbound_workflow_id")

            conn = ARIConnection(
                org_id,
                ari_endpoint,
                app_name,
                app_password,
                ws_client_name,
                inbound_workflow_id=inbound_workflow_id,
            )
            key = conn.connection_key

            active_keys.add(key)

            if key not in self._connections:
                # New configuration - start connection
                logger.info(
                    f"[ARI Manager] New ARI config for org {org_id}: {ari_endpoint}"
                )
                self._connections[key] = conn
                await conn.start()
            else:
                # Existing configuration - check if password or inbound_workflow_id changed
                existing = self._connections[key]
                if (
                    existing.app_password != app_password
                    or existing.inbound_workflow_id != inbound_workflow_id
                ):
                    logger.info(
                        f"[ARI Manager] Config changed for org {org_id}, reconnecting..."
                    )
                    await existing.stop()
                    self._connections[key] = conn
                    await conn.start()

        # Stop connections for removed configurations
        removed_keys = set(self._connections.keys()) - active_keys
        for key in removed_keys:
            conn = self._connections.pop(key)
            logger.info(
                f"[ARI Manager] Removing connection for org {conn.organization_id}"
            )
            await conn.stop()

        if active_configs:
            logger.info(
                f"[ARI Manager] Active connections: {len(self._connections)} "
                f"(orgs: {[c['organization_id'] for c in active_configs]})"
            )
        else:
            logger.debug("[ARI Manager] No ARI configurations found")

    async def _load_ari_configs(self) -> list:
        """Load all ARI telephony configurations from the database."""
        rows = await db_client.get_configurations_by_provider(
            OrganizationConfigurationKey.TELEPHONY_CONFIGURATION.value, "ari"
        )

        configs = []
        for row in rows:
            org_id = row["organization_id"]
            value = row["value"]

            ari_endpoint = value.get("ari_endpoint")
            app_name = value.get("app_name")
            app_password = value.get("app_password")
            ws_client_name = value.get("ws_client_name", "")

            if not all([ari_endpoint, app_name, app_password]):
                logger.warning(
                    f"[ARI Manager] Incomplete ARI config for org {org_id}, skipping"
                )
                continue

            if not ws_client_name:
                logger.warning(
                    f"[ARI Manager] Missing ws_client_name for org {org_id}, "
                    f"externalMedia WebSocket won't work"
                )

            configs.append(
                {
                    "organization_id": org_id,
                    "ari_endpoint": ari_endpoint,
                    "app_name": app_name,
                    "app_password": app_password,
                    "ws_client_name": ws_client_name,
                    "inbound_workflow_id": value.get("inbound_workflow_id"),
                }
            )

        return configs


async def main():
    """Entry point for the ARI manager process."""
    manager = ARIManager()

    # Handle graceful shutdown
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def signal_handler():
        logger.info("Received shutdown signal")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    # Start manager in background
    manager_task = asyncio.create_task(manager.start())

    # Wait for shutdown signal
    await shutdown_event.wait()

    # Clean up
    await manager.stop()
    manager_task.cancel()
    try:
        await manager_task
    except asyncio.CancelledError:
        pass

    logger.info("ARI Manager exited cleanly")


if __name__ == "__main__":
    asyncio.run(main())
