"""ARI-specific Stasis connection for use by ARI Manager.

This connection has direct access to the ARI client and manages
the actual Asterisk channels, bridges, and RTP setup.
"""

import json
import os
import uuid
from typing import Optional

import httpx
from loguru import logger

from api.services.telephony.ari_client import AsyncARIClient, Bridge, Channel
from api.services.telephony.ari_client_singleton import ari_client_singleton
from pipecat.utils.base_object import BaseObject


class ARIManagerConnection(BaseObject):
    """ARI Manager's connection that directly controls Asterisk resources.

    This class is used only by the ARI Manager process and has full
    access to the ARI client for creating bridges, channels, etc.
    """

    def __init__(
        self,
        caller_channel: Channel,
        host: str,
        port: int,
    ) -> None:
        """Initialize ARI Stasis connection.

        Args:
            caller_channel: The caller's channel object.
            host: Host address for RTP transport.
            port: Port number for RTP transport.
        """
        super().__init__()

        # External dependencies.
        self._host: str = host
        self._port: int = port

        # Store channel IDs instead of Channel objects to avoid stale references
        self.caller_channel_id: str = caller_channel.id
        self.em_channel_id: Optional[str] = None  # externalMedia channel ID

        # Store bridge ID to avoid stale references after reconnection
        self.bridge_id: Optional[str] = None

        # RTP addressing information
        self.local_addr = ("0.0.0.0", port)
        self.remote_addr = None

        # Internal state.
        self._closed: bool = False
        self._is_connected: bool = False

    def is_connected(self) -> bool:
        """Check if the connection is established."""
        return self._is_connected and not self._closed

    @property
    def _ari(self) -> Optional[AsyncARIClient]:
        """Get the current ARI client from singleton."""
        return ari_client_singleton.get_client()

    async def _get_channel(self, channel_id: str) -> Optional[Channel]:
        """Safely get a channel object by ID.

        Returns None if the channel doesn't exist or can't be fetched.
        """
        if not channel_id:
            return None
        try:
            # Get current client from singleton
            client = self._ari
            if not client:
                logger.warning(
                    f"Cannot get channel {channel_id} - No ARI client available"
                )
                return None
            # Check if the session is still active
            if not client._session or client._session.closed:
                logger.warning(
                    f"Cannot get channel {channel_id} - ARI session is closed"
                )
                return None
            return await client.channels.get(channelId=channel_id)
        except Exception as e:
            logger.warning(f"Could not get channel {channel_id} - {e}")
            return None

    async def _get_bridge(self, bridge_id: str) -> Optional[Bridge]:
        """Safely get a bridge object by ID.

        Returns None if the bridge doesn't exist or can't be fetched.
        """
        if not bridge_id:
            return None
        try:
            # Get current client from singleton
            client = self._ari
            if not client:
                logger.warning(
                    f"Cannot get bridge {bridge_id} - No ARI client available"
                )
                return None
            # Check if the session is still active
            if not client._session or client._session.closed:
                logger.warning(f"Cannot get bridge {bridge_id} - ARI session is closed")
                return None
            return await client.bridges.get(bridgeId=bridge_id)
        except Exception as e:
            logger.warning(f"Could not get bridge {bridge_id}: {e}")
            return None

    async def _cleanup_resources(self):
        """Clean up external media channel and bridge."""
        # Cleanup external media channel
        try:
            if self.em_channel_id:
                em_channel = await self._get_channel(self.em_channel_id)
                if em_channel:
                    await em_channel.hangup()
                    logger.debug(
                        f"channelID: {self.em_channel_id} Hung up external media"
                    )
                self.em_channel_id = None
        except Exception as exc:
            logger.warning(
                f"Failed to hang-up externalMedia channel: {self.em_channel_id}"
                f"Error: {exc}"
            )

        # Cleanup bridge
        try:
            if self.bridge_id:
                bridge = await self._get_bridge(self.bridge_id)
                if bridge:
                    await bridge.destroy()
                    logger.debug(f"bridgeID: {self.bridge_id} Destroyed bridge")
                self.bridge_id = None
        except Exception as exc:
            logger.warning(f"Failed to destroy bridge: {self.bridge_id}Error: {exc}")

    async def _sync_call_data(self, call_transfer_context: dict):
        """Sync call data to ARI_DATA_SYNCING_URI."""
        if not os.getenv("ARI_DATA_SYNCING_URI"):
            return

        lead_id = call_transfer_context.get("lead_id")
        status = call_transfer_context.get("disposition")

        #  {'lead_id': '299154', 'disposition': 'VM', 'agent_name': 'Alex', 'decision_maker': 'False', 'employment': 'N/A', 'debts': 'N/A', 'number_of_credit_cards': 'N/A', 'time': '2025-08-07T13:16:02-04:00'}

        full_name = call_transfer_context.get("full_name", "")
        phone = call_transfer_context.get("phone", "")
        debts = call_transfer_context.get("debts", "")
        employment = call_transfer_context.get("employment", "")
        time = call_transfer_context.get("time", "")

        comment = f"Type:Qualified!NName:{full_name}!NPhone:{phone}!NDebts:{debts}!NCC:N/A!NDM:Yes!NEmployment:{employment}!NTime:{time}!NVendor Id:!NStatus:{status}"

        try:
            if lead_id and status:
                ari_data_uri = os.getenv("ARI_DATA_SYNCING_URI")
                # Add URL params to the base URL
                sync_url = f"{ari_data_uri}&lead_id={lead_id}&status={status}&comments={comment}"

                logger.debug(
                    f"channelID: {self.caller_channel_id} Syncing data to ARI_DATA_SYNCING_URI: {sync_url}"
                )

                async with httpx.AsyncClient() as client:
                    response = await client.post(sync_url, timeout=10.0)
                    response.raise_for_status()
                    logger.info(
                        f"channelID: {self.caller_channel_id} Successfully synced data for lead_id: {lead_id} with status: {status}"
                    )
            else:
                logger.warning(
                    f"channelID: {self.caller_channel_id} Missing lead_id or status for syncing"
                )
        except Exception as e:
            logger.error(
                f"channelID: {self.caller_channel_id} Failed to sync data to ARI_DATA_SYNCING_URI: {e}"
            )

    async def disconnect(self):
        """Instruct Asterisk to hang-up the call and perform cleanup."""
        if self._closed:
            return

        # Lets mark it as closed so that when we receive StasisEnd, we don't
        # try to cleanup resource again
        self._closed = True

        # Clean up resources first
        await self._cleanup_resources()

        try:
            if self.caller_channel_id:
                caller_channel = await self._get_channel(self.caller_channel_id)
                if caller_channel:
                    logger.debug(
                        f"channelID: {self.caller_channel_id} Hanging up caller channel"
                    )
                    await caller_channel.hangup()
        except Exception:
            logger.exception("Failed to hangup caller channel")

    async def transfer(self, call_transfer_context: dict):
        """Transfer the call by continuing in dialplan with extracted variables."""
        if self._closed:
            return

        # Lets mark it as closed so that when we receive StasisEnd, we don't
        # try to cleanup resource again
        self._closed = True

        try:
            # Clean up resources before transferring
            await self._cleanup_resources()

            if self.caller_channel_id:
                caller_channel = await self._get_channel(self.caller_channel_id)
                if caller_channel:
                    logger.debug(
                        f"channelID: {self.caller_channel_id} User qualified, continuing in dialplan "
                        f"REMOTE_DISPO_CALL_VARIABLES: {json.dumps(call_transfer_context)}"
                    )

                    # Sync data to ARI_DATA_SYNCING_URI
                    await self._sync_call_data(
                        call_transfer_context=call_transfer_context
                    )

                    await caller_channel.continueInDialplan()
        except Exception:
            logger.exception("Failed to transfer caller channel")

    async def setup_call(self):
        """Setup the bridge and external media channel.

        This must be called after initialization to establish the connection.
        """
        await self._setup_call(self._host, self._port)

    async def _setup_call(self, host: str, port: int):
        """Create externalMedia + bridge and notify that the call is connected."""
        try:
            em_channel_id = str(uuid.uuid4())
            logger.debug(
                f"channelID: {em_channel_id} Creating externalMedia channel on {host}:{port}"
            )

            client = self._ari
            if not client:
                raise RuntimeError("No ARI client available")

            em_channel = await client.channels.externalMedia(
                app=client.app,
                channelId=em_channel_id,
                external_host=f"{host}:{port}",
                format="ulaw",
                direction="both",
            )

            # Store the channel ID
            self.em_channel_id = em_channel.id

            # Create a mixing bridge and add both legs.
            bridge = await client.bridges.create(type="mixing")
            self.bridge_id = bridge.id
            # Add channels individually as AsyncARIClient expects single channel per call
            await bridge.addChannel(channel=self.caller_channel_id)
            await bridge.addChannel(channel=self.em_channel_id)

            # TODO: Figure out how can we get the remote public IP. Till then
            # just pick it from the environment variable
            # Get RTP addressing information
            # ip = await em_channel.getChannelVar(
            #     variable="UNICASTRTP_LOCAL_ADDRESS"
            # )
            port = await em_channel.getChannelVar(variable="UNICASTRTP_LOCAL_PORT")

            self.remote_addr = (
                os.environ.get("ASTERISK_REMOTE_IP"),
                int(port["value"]),
            )

            logger.debug(
                f"channelID: {self.caller_channel_id} ARIManagerConnection connection resources ready "
                f"(bridgeID: {self.bridge_id}), (emChannelID: {self.em_channel_id})"
                f"remote address: {self.remote_addr}, local address: {self.local_addr}"
            )

            self._is_connected = True

        except Exception as exc:
            logger.exception(f"Error setting up ARIManagerConnection: {exc}")
            await self._cleanup_resources()

    async def notify_channel_end(self):
        """Notify that a channel has ended. Received after we get StasisEnd on the caller channel"""
        if self._closed:
            return

        self._closed = True
        self._is_connected = False

        # Cleanup resources using the shared method
        await self._cleanup_resources()

    def __repr__(self):
        """Return string representation of connection."""
        return (
            f"<ARIManagerConnection id={self.id} caller={self.caller_channel_id} "
            f"em={self.em_channel_id} bridge={self.bridge_id} state={'closed' if self._closed else 'open'}>"
        )
