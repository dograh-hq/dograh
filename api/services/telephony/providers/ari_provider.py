"""
Asterisk ARI (Asterisk REST Interface) implementation of the TelephonyProvider interface.

Uses ARI REST API to originate calls into a Stasis application.
The ARI WebSocket event listener runs as a separate process (ari_manager.py).
"""

import json
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp
from fastapi import HTTPException
from loguru import logger

from api.db import db_client
from api.enums import WorkflowRunMode
from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    TelephonyProvider,
)

if TYPE_CHECKING:
    from fastapi import WebSocket


class ARIProvider(TelephonyProvider):
    """
    Asterisk ARI implementation of TelephonyProvider.

    Uses ARI REST API for call control and relies on a separate
    ari_manager process for WebSocket event listening.
    """

    PROVIDER_NAME = WorkflowRunMode.ARI.value
    WEBHOOK_ENDPOINT = None  # ARI uses WebSocket events, not webhooks

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize ARIProvider with configuration.

        Args:
            config: Dictionary containing:
                - ari_endpoint: ARI base URL (e.g., http://asterisk:8088)
                - app_name: Stasis application name
                - app_password: ARI user password
                - from_numbers: List of SIP extensions/numbers (optional)
        """
        self.ari_endpoint = config.get("ari_endpoint", "").rstrip("/")
        self.app_name = config.get("app_name", "")
        self.app_password = config.get("app_password", "")
        self.inbound_workflow_id = config.get("inbound_workflow_id")
        self.from_numbers = config.get("from_numbers", [])

        if isinstance(self.from_numbers, str):
            self.from_numbers = [self.from_numbers]

        self.base_url = f"{self.ari_endpoint}/ari"

    def _get_auth(self) -> aiohttp.BasicAuth:
        """Generate BasicAuth for ARI API requests."""
        return aiohttp.BasicAuth(self.app_name, self.app_password)

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        """
        Initiate an outbound call via ARI.

        Creates a channel in Asterisk using the ARI channels endpoint.
        The channel is placed into the Stasis application where
        the ari_manager will receive the StasisStart event.
        """
        if not self.validate_config():
            raise ValueError("ARI provider not properly configured")

        endpoint = f"{self.base_url}/channels"

        # Build the SIP endpoint string
        # to_number can be a SIP URI or extension
        if to_number.startswith("SIP/") or to_number.startswith("PJSIP/"):
            sip_endpoint = to_number
        else:
            # Default to PJSIP technology
            sip_endpoint = f"PJSIP/{to_number}"

        # Prepare channel creation data
        params = {
            "endpoint": sip_endpoint,
            "app": self.app_name,
            "appArgs": ",".join(
                filter(
                    None,
                    [
                        f"workflow_run_id={workflow_run_id}",
                        f"workflow_id={kwargs.get('workflow_id', '')}",
                        f"user_id={kwargs.get('user_id', '')}",
                    ],
                )
            ),
        }

        if from_number:
            params["callerId"] = from_number

        logger.info(
            f"[ARI] Initiating call to {sip_endpoint} "
            f"via app={self.app_name}, workflow_run_id={workflow_run_id}"
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                endpoint,
                params=params,
                auth=self._get_auth(),
            ) as response:
                response_text = await response.text()

                if response.status != 200:
                    logger.error(
                        f"[ARI] Channel creation failed: "
                        f"HTTP {response.status} - {response_text}"
                    )
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"Failed to create ARI channel: {response_text}",
                    )

                response_data = json.loads(response_text)
                channel_id = response_data.get("id", "")

                logger.info(
                    f"[ARI] Channel created: {channel_id} "
                    f"state={response_data.get('state')}"
                )

                return CallInitiationResult(
                    call_id=channel_id,
                    status=response_data.get("state", "created"),
                    provider_metadata={
                        "call_id": channel_id,
                        "channel_name": response_data.get("name", ""),
                    },
                    raw_response=response_data,
                )

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        """Get channel status from ARI."""
        if not self.validate_config():
            raise ValueError("ARI provider not properly configured")

        endpoint = f"{self.base_url}/channels/{call_id}"

        async with aiohttp.ClientSession() as session:
            async with session.get(endpoint, auth=self._get_auth()) as response:
                if response.status != 200:
                    error_data = await response.text()
                    raise Exception(f"Failed to get channel status: {error_data}")
                return await response.json()

    async def get_available_phone_numbers(self) -> List[str]:
        """Return configured extensions/numbers."""
        return self.from_numbers

    def validate_config(self) -> bool:
        """Validate ARI configuration."""
        return bool(self.ari_endpoint and self.app_name and self.app_password)

    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        """ARI does not use webhook signatures - events come via WebSocket."""
        return True

    async def get_webhook_response(
        self, workflow_id: int, user_id: int, workflow_run_id: int
    ) -> str:
        """ARI does not use webhook responses - call control is via REST API."""
        logger.warning(
            "get_webhook_response called for ARI - this should not happen. "
            "ARI uses REST API for call control, not webhooks."
        )
        return ""

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        """ARI/Asterisk does not provide call cost information."""
        return {
            "cost_usd": 0.0,
            "duration": 0,
            "status": "unknown",
            "error": "ARI does not support cost retrieval",
        }

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse ARI event data into generic status callback format.

        ARI events come from the WebSocket listener, not HTTP callbacks.
        """
        # Map ARI channel states to common status format
        state_map = {
            "Up": "answered",
            "Down": "completed",
            "Ringing": "ringing",
            "Ring": "ringing",
            "Busy": "busy",
            "Unavailable": "failed",
        }

        channel_state = data.get("channel", {}).get("state", "")
        event_type = data.get("type", "")

        # Determine status from event type
        if event_type == "StasisStart":
            status = "answered"
        elif event_type == "StasisEnd":
            status = "completed"
        elif event_type == "ChannelDestroyed":
            status = "completed"
        else:
            status = state_map.get(channel_state, channel_state.lower())

        channel = data.get("channel", {})
        return {
            "call_id": channel.get("id", ""),
            "status": status,
            "from_number": channel.get("caller", {}).get("number"),
            "to_number": channel.get("dialplan", {}).get("exten"),
            "direction": None,
            "duration": None,
            "extra": data,
        }

    async def handle_websocket(
        self,
        websocket: "WebSocket",
        workflow_id: int,
        user_id: int,
        workflow_run_id: int,
    ) -> None:
        """
        Handle WebSocket connection from ARI externalMedia channel.

        Unlike Twilio (which sends "connected" and "start" JSON messages),
        Asterisk chan_websocket starts streaming audio immediately.
        """
        from api.services.pipecat.run_pipeline import run_pipeline_ari

        # Get channel_id from workflow run context
        workflow_run = await db_client.get_workflow_run(workflow_run_id, user_id)
        channel_id = ""
        if workflow_run and workflow_run.gathered_context:
            channel_id = workflow_run.gathered_context.get("call_id", "")

        logger.info(
            f"[ARI] Starting pipeline for workflow_run {workflow_run_id}, channel={channel_id}"
        )

        await run_pipeline_ari(
            websocket, channel_id, workflow_id, workflow_run_id, user_id
        )

    # ======== INBOUND CALL METHODS ========

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        """
        ARI does not use HTTP webhooks for inbound calls.
        Inbound calls are received via the ARI WebSocket event listener.
        """
        return False

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        """Parse ARI event data into normalized inbound format."""
        channel = webhook_data.get("channel", {})
        caller = channel.get("caller", {})
        connected = channel.get("connected", {})

        return NormalizedInboundData(
            provider=ARIProvider.PROVIDER_NAME,
            call_id=channel.get("id", ""),
            from_number=caller.get("number", ""),
            to_number=channel.get("dialplan", {}).get("exten", ""),
            direction="inbound",
            call_status=channel.get("state", ""),
            account_id=None,
            raw_data=webhook_data,
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        """ARI doesn't use account IDs for validation."""
        return True

    def normalize_phone_number(self, phone_number: str) -> str:
        """Normalize phone number - ARI uses extensions as-is."""
        return phone_number or ""

    async def verify_inbound_signature(
        self, url: str, webhook_data: Dict[str, Any], signature: str
    ) -> bool:
        """ARI authenticates via WebSocket connection credentials, not signatures."""
        return True

    @staticmethod
    async def generate_inbound_response(
        websocket_url: str, workflow_run_id: int = None
    ) -> tuple:
        """ARI does not generate HTTP responses for inbound calls."""
        from fastapi import Response

        return Response(content="", status_code=204)

    @staticmethod
    def generate_error_response(error_type: str, message: str) -> tuple:
        """Generate a generic JSON error response."""
        from fastapi import Response

        return Response(
            content=json.dumps({"error": error_type, "message": message}),
            media_type="application/json",
        )

    @staticmethod
    def generate_validation_error_response(error_type) -> tuple:
        """Generate JSON error response for validation failures."""
        from fastapi import Response

        from api.errors.telephony_errors import TELEPHONY_ERROR_MESSAGES, TelephonyError

        message = TELEPHONY_ERROR_MESSAGES.get(
            error_type, TELEPHONY_ERROR_MESSAGES[TelephonyError.GENERAL_AUTH_FAILED]
        )

        return Response(
            content=json.dumps({"error": str(error_type), "message": message}),
            media_type="application/json",
        )

    # ======== CALL TRANSFER METHODS ========

    def supports_transfers(self) -> bool:
        """ARI supports call transfers via bridge manipulation."""
        return True

    async def transfer_call(
        self,
        destination: str,
        transfer_id: str,
        conference_name: str,
        timeout: int = 30,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Initiate ARI call transfer by creating an outbound channel to the destination.

        This method creates the destination channel and returns immediately. The transfer
        process completes asynchronously - success/failure is determined by ARI events
        and communicated through the transfer event system.

              Args:
                  destination: Destination phone number (SIP endpoint)
                  transfer_id: Unique identifier for this transfer attempt
                  conference_name: Conference name (unused in ARI, kept for interface compatibility)
                  timeout: Transfer timeout in seconds
                  **kwargs: Additional arguments

              Returns:
                  Dict containing:
                      - call_sid: Destination channel ID
                      - status: "initiated"
                      - provider: "ari"
                      - raw_response: Full ARI channel creation response
        """
        if not self.validate_config():
            raise ValueError("ARI provider not properly configured")

        logger.info(
            f"[ARI Transfer] Initiating transfer {transfer_id} to {destination} "
            f"(timeout: {timeout}s)"
        )

        # Import here to avoid circular dependency
        from api.services.telephony.call_transfer_manager import (
            get_call_transfer_manager,
        )
        from api.services.telephony.transfer_event_protocol import TransferContext

        # Store transfer context for event correlation
        call_transfer_manager = await get_call_transfer_manager()
        context = TransferContext(
            transfer_id=transfer_id,
            call_sid=None,  # Will be updated after channel creation
            target_number=destination,
            tool_uuid=kwargs.get("tool_uuid", ""),
            original_call_sid=kwargs.get("original_call_sid", ""),
            conference_name=conference_name,
            initiated_at=time.time(),
        )
        await call_transfer_manager.store_transfer_context(context, ttl=timeout + 10)

        # Build SIP endpoint
        if destination.startswith("SIP/") or destination.startswith("PJSIP/"):
            sip_endpoint = destination
        else:
            sip_endpoint = f"PJSIP/{destination}"

        # Build transfer appArgs for event correlation
        app_args = f"transfer,{transfer_id}"

        try:
            endpoint = f"{self.base_url}/channels"
            params = {
                "endpoint": sip_endpoint,
                "app": self.app_name,
                "appArgs": app_args,
                "timeout": timeout,  # Keep timeout for transfer calls
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    endpoint,
                    params=params,
                    auth=self._get_auth(),
                ) as response:
                    response_text = await response.text()

                    if response.status != 200:
                        error_msg = f"ARI channel creation failed: {response.status} {response_text}"
                        logger.error(f"[ARI Transfer] {error_msg}")
                        await call_transfer_manager.remove_transfer_context(transfer_id)
                        raise Exception(error_msg)

                    result = json.loads(response_text)

            destination_channel_id = result.get("id", "")
            if not destination_channel_id:
                logger.error(
                    f"[ARI Transfer] Failed to get channel ID from response: {result}"
                )
                await call_transfer_manager.remove_transfer_context(transfer_id)
                raise Exception("Failed to create destination channel")

            # Update transfer context with destination channel ID
            context.call_sid = destination_channel_id
            await call_transfer_manager.store_transfer_context(
                context, ttl=timeout + 10
            )

            # Store transfer channel mapping for event correlation
            await call_transfer_manager.store_transfer_channel_mapping(
                destination_channel_id, transfer_id
            )

            logger.info(
                f"[ARI Transfer] Originated destination channel {destination_channel_id} "
                f"for transfer {transfer_id}"
            )

            return {
                "call_sid": destination_channel_id,
                "status": "initiated",
                "provider": self.PROVIDER_NAME,
                "raw_response": result,
            }

        except Exception as e:
            logger.error(f"[ARI Transfer] Failed to originate transfer channel: {e}")
            await call_transfer_manager.remove_transfer_context(transfer_id)
            raise

    # ======== ARI-SPECIFIC METHODS ========

    async def hangup_channel(self, channel_id: str, reason: str = "normal") -> bool:
        """Hang up an ARI channel."""
        endpoint = f"{self.base_url}/channels/{channel_id}"
        params = {"reason_code": reason}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    endpoint, params=params, auth=self._get_auth()
                ) as response:
                    if response.status in (200, 204):
                        logger.info(f"[ARI] Channel {channel_id} hung up")
                        return True
                    else:
                        error = await response.text()
                        logger.error(
                            f"[ARI] Failed to hangup channel {channel_id}: {error}"
                        )
                        return False
        except Exception as e:
            logger.error(f"[ARI] Exception hanging up channel {channel_id}: {e}")
            return False

    async def answer_channel(self, channel_id: str) -> bool:
        """Answer an ARI channel."""
        endpoint = f"{self.base_url}/channels/{channel_id}/answer"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(endpoint, auth=self._get_auth()) as response:
                    if response.status in (200, 204):
                        logger.info(f"[ARI] Channel {channel_id} answered")
                        return True
                    else:
                        error = await response.text()
                        logger.error(
                            f"[ARI] Failed to answer channel {channel_id}: {error}"
                        )
                        return False
        except Exception as e:
            logger.error(f"[ARI] Exception answering channel {channel_id}: {e}")
            return False

    def get_ws_url(self) -> str:
        """Get the ARI WebSocket URL for event listening."""
        parsed = urlparse(self.ari_endpoint)
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        return (
            f"{ws_scheme}://{parsed.netloc}/ari/events"
            f"?api_key={self.app_name}:{self.app_password}"
            f"&app={self.app_name}"
            f"&subscribeAll=true"
        )
