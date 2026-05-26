"""3CX telephony provider — Asterisk PJSIP trunk to a 3CX cloud PBX.

Functionally a specialisation of ARI: the runtime call control flow is
identical (REST originate + Stasis externalMedia), but the provider
carries the 3CX trunk credentials and matches inbound calls back to a
configuration by ``extension``.

We duplicate the ARI provider body rather than subclassing it because
``providers/AGENTS.md`` forbids cross-provider imports. A future
``services/telephony/asterisk_base.py`` extraction should consolidate the
shared logic.
"""

import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp
from fastapi import HTTPException
from loguru import logger

from api.db import db_client
from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    TelephonyProvider,
)

if TYPE_CHECKING:
    from fastapi import WebSocket


class ThreeCxProvider(TelephonyProvider):
    """3CX-over-Asterisk implementation of TelephonyProvider."""

    PROVIDER_NAME = "three_cx"
    WEBHOOK_ENDPOINT = None  # 3CX uses WebSocket events via Asterisk, not webhooks

    def __init__(self, config: Dict[str, Any]):
        """Initialise from the normalised config dict produced by _config_loader."""
        self.ari_endpoint = (config.get("ari_endpoint") or "").rstrip("/")
        self.app_name = config.get("app_name", "")
        self.app_password = config.get("app_password", "")
        self.from_numbers = config.get("from_numbers", [])

        # 3CX trunk identity — carried for inbound matching and for the
        # provisioning hook to address the right ARA rows. Not used at
        # runtime by REST call control (Asterisk owns the SIP leg).
        self.sip_domain = (config.get("sip_domain") or "").strip().lower()
        self.extension = (config.get("extension") or "").strip()
        self.strip_prefix = config.get("strip_prefix", "")

        if isinstance(self.from_numbers, str):
            self.from_numbers = [self.from_numbers]

        self.base_url = f"{self.ari_endpoint}/ari"

    def _get_auth(self) -> aiohttp.BasicAuth:
        return aiohttp.BasicAuth(self.app_name, self.app_password)

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        """Originate an outbound call via the bridging Asterisk.

        The dialled number is routed through the generated outbound dialplan
        context (``<endpoint_id>-outbound``) so the ``strip_prefix`` regex
        the admin saved is honoured at the dialplan layer, not in Python.
        """
        if not self.validate_config():
            raise ValueError("3CX provider not properly configured")

        endpoint = f"{self.base_url}/channels"

        # Local-channel into the generated outbound context, which contains
        # the strip_prefix-aware Dial(PJSIP/...@<endpoint_id>) row.
        endpoint_id = self._endpoint_id()
        sip_endpoint = f"Local/{to_number}@{endpoint_id}-outbound"

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
            f"[3CX] Initiating call to {to_number} via {sip_endpoint} "
            f"(workflow_run_id={workflow_run_id})"
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
                        f"[3CX] Channel creation failed: "
                        f"HTTP {response.status} - {response_text}"
                    )
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"Failed to create 3CX channel: {response_text}",
                    )

                response_data = json.loads(response_text)
                channel_id = response_data.get("id", "")

                return CallInitiationResult(
                    call_id=channel_id,
                    status=response_data.get("state", "created"),
                    caller_number=from_number,
                    provider_metadata={
                        "call_id": channel_id,
                        "channel_name": response_data.get("name", ""),
                    },
                    raw_response=response_data,
                )

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        if not self.validate_config():
            raise ValueError("3CX provider not properly configured")
        url = f"{self.base_url}/channels/{call_id}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, auth=self._get_auth()) as response:
                if response.status != 200:
                    raise Exception(
                        f"Failed to get channel status: {await response.text()}"
                    )
                return await response.json()

    async def get_available_phone_numbers(self) -> List[str]:
        return self.from_numbers

    def validate_config(self) -> bool:
        """Asterisk-side credentials are the only ones required at runtime.

        3CX-side credentials (``sip_password`` etc.) are consumed at save time
        by the provisioning hook; they're not needed for REST call control.
        """
        return bool(self.ari_endpoint and self.app_name and self.app_password)

    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        return True

    async def get_webhook_response(
        self, workflow_id: int, user_id: int, workflow_run_id: int
    ) -> str:
        logger.warning(
            "get_webhook_response called for 3CX — not applicable, "
            "control plane is Asterisk REST."
        )
        return ""

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        return {
            "cost_usd": 0.0,
            "duration": 0,
            "status": "unknown",
            "error": "3CX does not surface call cost to Dograh",
        }

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
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

        if event_type == "StasisStart":
            status = "answered"
        elif event_type in ("StasisEnd", "ChannelDestroyed"):
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
        from api.services.pipecat.run_pipeline import run_pipeline_telephony

        workflow_run = await db_client.get_workflow_run(workflow_run_id, user_id)
        channel_id = ""
        if workflow_run and workflow_run.gathered_context:
            channel_id = workflow_run.gathered_context.get("call_id", "")

        logger.info(
            f"[3CX] Starting pipeline for workflow_run {workflow_run_id}, "
            f"channel={channel_id}"
        )

        await run_pipeline_telephony(
            websocket,
            provider_name=self.PROVIDER_NAME,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
            call_id=channel_id,
            transport_kwargs={"channel_id": channel_id},
        )

    # ======== INBOUND CALL METHODS ========

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        """3CX uses no HTTP webhook layer — inbound arrives via Stasis events."""
        return False

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        """Parse a Stasis event into normalised inbound data.

        ``account_id`` is populated with the dialled extension so the
        inbound dispatcher can match it against
        ``credentials['extension']`` and pick the right 3CX configuration
        when multiple coexist in one org.
        """
        channel = webhook_data.get("channel", {})
        caller = channel.get("caller", {})
        exten = channel.get("dialplan", {}).get("exten", "")

        return NormalizedInboundData(
            provider=ThreeCxProvider.PROVIDER_NAME,
            call_id=channel.get("id", ""),
            from_number=caller.get("number", ""),
            to_number=exten,
            direction="inbound",
            call_status=channel.get("state", ""),
            account_id=exten or None,
            raw_data=webhook_data,
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        """Match the dialled extension against the saved trunk's extension."""
        stored = (config_data or {}).get("extension")
        if not stored or not webhook_account_id:
            return False
        return stored == webhook_account_id

    async def verify_inbound_signature(
        self,
        url: str,
        webhook_data: Dict[str, Any],
        headers: Dict[str, str],
        body: str = "",
    ) -> bool:
        """3CX authenticates via the Asterisk WebSocket creds; no payload signature."""
        return True

    async def start_inbound_stream(
        self,
        *,
        websocket_url: str,
        workflow_run_id: int,
        normalized_data,
        backend_endpoint: str,
    ):
        from fastapi import Response

        return Response(content="", status_code=204)

    @staticmethod
    def generate_error_response(error_type: str, message: str) -> tuple:
        from fastapi import Response

        return Response(
            content=json.dumps({"error": error_type, "message": message}),
            media_type="application/json",
        )

    # ======== CALL TRANSFER METHODS ========

    def supports_transfers(self) -> bool:
        return True

    async def transfer_call(
        self,
        destination: str,
        transfer_id: str,
        conference_name: str,
        timeout: int = 30,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Transfer by originating a destination channel and bridge-swapping it in."""
        if not self.validate_config():
            raise ValueError("3CX provider not properly configured")

        from api.services.telephony.call_transfer_manager import (
            get_call_transfer_manager,
        )

        call_transfer_manager = await get_call_transfer_manager()

        endpoint_id = self._endpoint_id()
        sip_endpoint = f"Local/{destination}@{endpoint_id}-outbound"

        app_args = f"transfer,{transfer_id}"

        try:
            endpoint = f"{self.base_url}/channels"
            params = {
                "endpoint": sip_endpoint,
                "app": self.app_name,
                "appArgs": app_args,
                "timeout": timeout,
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    endpoint, params=params, auth=self._get_auth()
                ) as response:
                    response_text = await response.text()
                    if response.status != 200:
                        await call_transfer_manager.remove_transfer_context(
                            transfer_id
                        )
                        raise Exception(
                            f"3CX channel creation failed: "
                            f"{response.status} {response_text}"
                        )
                    result = json.loads(response_text)

            destination_channel_id = result.get("id", "")
            if not destination_channel_id:
                await call_transfer_manager.remove_transfer_context(transfer_id)
                raise Exception("Failed to create destination channel")

            await call_transfer_manager.store_transfer_channel_mapping(
                destination_channel_id, transfer_id
            )

            return {
                "call_sid": destination_channel_id,
                "status": "initiated",
                "provider": self.PROVIDER_NAME,
                "raw_response": result,
            }

        except Exception as e:
            logger.error(f"[3CX Transfer] Failed: {e}")
            await call_transfer_manager.remove_transfer_context(transfer_id)
            raise

    # ======== 3CX-SPECIFIC HELPERS ========

    def _endpoint_id(self) -> str:
        """Globally unique Asterisk endpoint name for this trunk.

        Matches the naming used by the provisioning hook so dialplan + REST
        agree on which PJSIP endpoint to address. See provisioning.py.
        """
        from .provisioning import endpoint_id_for

        return endpoint_id_for(self.sip_domain, self.extension)

    async def hangup_channel(self, channel_id: str, reason: str = "normal") -> bool:
        endpoint = f"{self.base_url}/channels/{channel_id}"
        params = {"reason_code": reason}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    endpoint, params=params, auth=self._get_auth()
                ) as response:
                    if response.status in (200, 204):
                        return True
                    logger.error(
                        f"[3CX] Failed to hangup channel {channel_id}: "
                        f"{await response.text()}"
                    )
                    return False
        except Exception as e:
            logger.error(f"[3CX] Exception hanging up channel {channel_id}: {e}")
            return False

    async def answer_channel(self, channel_id: str) -> bool:
        endpoint = f"{self.base_url}/channels/{channel_id}/answer"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(endpoint, auth=self._get_auth()) as response:
                    return response.status in (200, 204)
        except Exception as e:
            logger.error(f"[3CX] Exception answering channel {channel_id}: {e}")
            return False

    def get_ws_url(self) -> str:
        """ARI WebSocket URL for the standalone event listener (ari_manager)."""
        parsed = urlparse(self.ari_endpoint)
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        return (
            f"{ws_scheme}://{parsed.netloc}/ari/events"
            f"?api_key={self.app_name}:{self.app_password}"
            f"&app={self.app_name}"
            f"&subscribeAll=true"
        )
