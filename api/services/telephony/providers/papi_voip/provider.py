"""
Papi Voip implementation of the TelephonyProvider interface.
Handles Papi GO Cloud voice sessions.
"""

import asyncio
import json
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import aiohttp
from fastapi import HTTPException
from loguru import logger
from starlette.websockets import WebSocketDisconnect
from starlette.websockets import WebSocketState

from api.enums import TelephonyCallStatus, WorkflowRunMode
from api.services.telephony import ws_auth
from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    ProviderSyncResult,
    TelephonyProvider,
)
from api.utils.common import get_backend_endpoints

if TYPE_CHECKING:
    from fastapi import WebSocket


PAPI_MEDIA_STREAM_CONNECT_TIMEOUT_SECS = 45
PAPI_MEDIA_STREAM_RETRY_DELAY_SECS = 1


class _AiohttpClientWebSocketAdapter:
    """Adapt an aiohttp client WebSocket to the FastAPI transport interface."""

    def __init__(
        self,
        websocket: aiohttp.ClientWebSocketResponse,
        first_message: aiohttp.WSMessage | None = None,
    ):
        self._websocket = websocket
        self._first_message = first_message
        self.client_state = WebSocketState.CONNECTED
        self.application_state = WebSocketState.CONNECTED

    async def receive(self) -> dict[str, Any]:
        message = self._first_message or await self._websocket.receive()
        self._first_message = None
        if message.type == aiohttp.WSMsgType.BINARY:
            return {"type": "websocket.receive", "bytes": message.data}
        if message.type == aiohttp.WSMsgType.TEXT:
            return {"type": "websocket.receive", "text": message.data}
        if message.type == aiohttp.WSMsgType.ERROR:
            raise message.data or RuntimeError("Papi Voip media socket error")

        self.client_state = WebSocketState.DISCONNECTED
        self.application_state = WebSocketState.DISCONNECTED
        code = message.data if isinstance(message.data, int) else 1000
        return {"type": "websocket.disconnect", "code": code}

    async def send_bytes(self, data: bytes) -> None:
        await self._websocket.send_bytes(data)

    async def send_text(self, data: str) -> None:
        await self._websocket.send_str(data)

    async def close(self) -> None:
        self.client_state = WebSocketState.DISCONNECTED
        self.application_state = WebSocketState.DISCONNECTED
        await self._websocket.close()


class PapiVoipProvider(TelephonyProvider):
    """
    Papi Voip implementation of TelephonyProvider.
    Uses Papi GO Cloud API to bridge voice calls.
    """

    PROVIDER_NAME = WorkflowRunMode.PAPI_VOIP.value
    WEBHOOK_ENDPOINT = "papi-voip-webhook"

    def __init__(self, config: Dict[str, Any]):
        self.base_url = (config.get("base_url") or "https://api.papi.api.br").rstrip("/")
        self.api_key = config.get("api_key")
        self.instance_id = config.get("instance_id")
        self.from_numbers = config.get("from_numbers", [])
        self.default_from_number = config.get("default_from_number")
        self._media_stream_tasks: set[asyncio.Task[Any]] = set()

        if isinstance(self.from_numbers, str):
            self.from_numbers = [self.from_numbers]

    def _build_call_stream_url(self, call_id: str) -> str:
        return (
            f"{self.base_url}/api/instances/{self.instance_id}/voice/calls/{call_id}/stream"
        )

    def _build_auth_headers(self, *, include_content_type: bool = False) -> dict[str, str]:
        headers = {
            "x-api-key": self.api_key,
            "apikey": self.api_key,
        }
        if include_content_type:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _get_call_id_from_response(response_data: dict[str, Any]) -> str | None:
        """Return the PAP call identifier from its supported dial response shapes."""
        for key in ("call_id", "callId", "sid", "id"):
            value = response_data.get(key)
            if value:
                return str(value)

        call = response_data.get("call")
        if isinstance(call, dict):
            for key in ("call_id", "callId", "sid", "id"):
                value = call.get(key)
                if value:
                    return str(value)

        return None

    def _schedule_media_stream_task(
        self,
        *,
        workflow_id: int,
        organization_id: int,
        workflow_run_id: int,
        call_id: str,
    ) -> None:
        logger.info(
            "Scheduling Papi Voip media stream task for "
            f"workflow_run {workflow_run_id}, call_id={call_id}"
        )
        task = asyncio.create_task(
            self._connect_outbound_media_stream(
                workflow_id=workflow_id,
                organization_id=organization_id,
                workflow_run_id=workflow_run_id,
                call_id=call_id,
            ),
            name=f"papi-voip-media-{workflow_run_id}",
        )
        self._media_stream_tasks.add(task)
        task.add_done_callback(self._media_stream_tasks.discard)
        task.add_done_callback(self._log_media_stream_task_result)

    def _log_media_stream_task_result(self, task: asyncio.Task[Any]) -> None:
        with suppress(asyncio.CancelledError):
            exc = task.exception()
            if exc:
                logger.error(f"Papi Voip media stream task failed: {exc}")

    async def _wait_for_answered_media(
        self, websocket: aiohttp.ClientWebSocketResponse, workflow_run_id: int
    ) -> aiohttp.WSMessage:
        """Wait for the first PCM frame, which PAP starts sending after answer."""
        while True:
            message = await asyncio.wait_for(
                websocket.receive(), timeout=PAPI_MEDIA_STREAM_CONNECT_TIMEOUT_SECS
            )
            if message.type == aiohttp.WSMsgType.BINARY:
                logger.info(
                    f"Papi Voip media started after answer for workflow_run {workflow_run_id}"
                )
                return message
            if message.type == aiohttp.WSMsgType.ERROR:
                raise message.data or RuntimeError("Papi Voip media socket error")
            if message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                raise RuntimeError("Papi Voip media socket closed before the call was answered")

    async def _connect_outbound_media_stream(
        self,
        *,
        workflow_id: int,
        organization_id: int,
        workflow_run_id: int,
        call_id: str,
    ) -> None:
        from api.services.pipecat.run_pipeline import run_pipeline_telephony

        stream_url = self._build_call_stream_url(call_id)
        headers = self._build_auth_headers()
        deadline = asyncio.get_running_loop().time() + PAPI_MEDIA_STREAM_CONNECT_TIMEOUT_SECS
        last_error: Exception | None = None
        attempt = 0

        while True:
            attempt += 1
            try:
                logger.info(
                    "Connecting to Papi Voip media stream for "
                    f"workflow_run {workflow_run_id}, call_id={call_id}, attempt={attempt}"
                )
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(
                        stream_url,
                        headers=headers,
                        heartbeat=30,
                        timeout=None,
                    ) as websocket:
                        first_audio_frame = await self._wait_for_answered_media(
                            websocket, workflow_run_id
                        )
                        logger.info(
                            f"Papi Voip media stream connected for workflow_run {workflow_run_id}"
                        )
                        await run_pipeline_telephony(
                            _AiohttpClientWebSocketAdapter(websocket, first_audio_frame),
                            provider_name=self.PROVIDER_NAME,
                            workflow_id=workflow_id,
                            workflow_run_id=workflow_run_id,
                            organization_id=organization_id,
                            call_id=call_id,
                            transport_kwargs={"call_id": call_id},
                        )
                        return
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Papi Voip media stream connect attempt failed for "
                    f"workflow_run {workflow_run_id}, call_id={call_id}, "
                    f"attempt={attempt}: {exc}"
                )
                if asyncio.get_running_loop().time() >= deadline:
                    break
                await asyncio.sleep(PAPI_MEDIA_STREAM_RETRY_DELAY_SECS)

        logger.error(
            "Papi Voip media stream did not become available for "
            f"workflow_run {workflow_run_id}: {last_error}"
        )

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        """
        Initiate an outbound call via Papi Voip API.
        """
        if not self.validate_config():
            raise ValueError("Papi Voip provider not properly configured")

        endpoint = f"{self.base_url}/api/instances/{self.instance_id}/voice/dial"

        from_number = self.select_from_number(from_number)
        logger.info(f"Selected Papi Voip phone number {from_number} for outbound call")

        payload = {
            "to": to_number.lstrip("+"),
            "webhook_url": webhook_url,
            "workflow_run_id": workflow_run_id,
        }

        # Papi suporta dial customizado caso informado no field 'from'
        if from_number:
            payload["from"] = from_number

        # Combine with other provider specific arguments
        payload.update(kwargs)

        headers = self._build_auth_headers(include_content_type=True)

        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=payload, headers=headers) as response:
                if response.status not in (200, 201):
                    error_data = await response.text()
                    raise HTTPException(
                        status_code=response.status, detail=f"Papi API dial failed: {error_data}"
                    )

                response_data = await response.json()
                
                # PAP can acknowledge dial without including the call ID. Its
                # documented active stream endpoint identifies the current call.
                call_id = self._get_call_id_from_response(response_data) or "active"

                workflow_id = kwargs.get("workflow_id")
                organization_id = kwargs.get("organization_id")
                if workflow_id and organization_id and workflow_run_id:
                    self._schedule_media_stream_task(
                        workflow_id=workflow_id,
                        organization_id=organization_id,
                        workflow_run_id=workflow_run_id,
                        call_id=call_id,
                    )
                
                return CallInitiationResult(
                    call_id=call_id,
                    status=response_data.get("status", "initiated"),
                    caller_number=from_number,
                    provider_metadata={"call_id": call_id},
                    raw_response=response_data,
                )

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        """Check Papi call status."""
        endpoint = f"{self.base_url}/api/instances/{self.instance_id}/voice/calls/{call_id}"
        headers = self._build_auth_headers()
        async with aiohttp.ClientSession() as session:
            async with session.get(endpoint, headers=headers) as response:
                if response.status != 200:
                    return {"status": "error"}
                return await response.json()

    async def get_available_phone_numbers(self) -> List[str]:
        return self.from_numbers

    def validate_config(self) -> bool:
        return bool(self.api_key and self.instance_id)

    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        # Assumes Papi doesn't enforce signature yet on its callbacks, relying on token embedded in stream url
        return True

    async def get_webhook_response(
        self, workflow_id: int, organization_id: int, workflow_run_id: int
    ) -> str:
        """
        Generate JSON response for an incoming Papi Voip call webhook answering hook.
        """
        _, wss_backend_endpoint = await get_backend_endpoints()
        ws_url = ws_auth.build_media_ws_url(
            wss_backend_endpoint, workflow_id, organization_id, workflow_run_id
        )
        return json.dumps({"action": "stream", "stream_url": ws_url})

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        return {"cost_usd": 0.0, "duration": 0, "status": "unknown"}

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        status_mapper = {
            "completed": TelephonyCallStatus.COMPLETED,
            "failed": TelephonyCallStatus.FAILED,
            "ringing": TelephonyCallStatus.RINGING,
            "in-progress": TelephonyCallStatus.IN_PROGRESS,
            "busy": TelephonyCallStatus.BUSY,
            "no-answer": TelephonyCallStatus.NO_ANSWER,
            "canceled": TelephonyCallStatus.CANCELED,
            "answered": TelephonyCallStatus.ANSWERED
        }
        raw_status = data.get("status", "")
        return {
            "call_id": data.get("call_id"),
            "status": status_mapper.get(raw_status, raw_status),
            "from_number": data.get("from"),
            "to_number": data.get("to"),
            "direction": data.get("direction"),
            "duration": data.get("duration"),
            "extra": data,
        }

    async def handle_websocket(
        self,
        websocket: "WebSocket",
        workflow_id: int,
        organization_id: int,
        workflow_run_id: int,
    ) -> None:
        """
        Handle Papi Voip WebSocket connection for real-time call audio.

        Papi Voip sends:
        1. {"ready": true, "frameBytes": 1920, "frameSamples": 960, "sampleRate": 16000}
        2. Then raw PCM bytes
        """
        from api.db import db_client
        from api.services.pipecat.run_pipeline import run_pipeline_telephony

        try:
            # Wait for "ready" event from Papi GO instance
            first_msg = await websocket.receive_text()
            try:
                msg = json.loads(first_msg)
            except json.JSONDecodeError:
                logger.error(f"Expected JSON ready payload from Papi Voip, got: {first_msg}")
                await websocket.close(code=4400, reason="Expected JSON ready payload")
                return

            if not msg.get("ready"):
                logger.error(f"Expected 'ready' true in Papi Voip handshake, got: {msg}")
                await websocket.close(code=4400, reason="Expected ready event")
                return

            logger.debug(
                f"Papi Voip WebSocket connected for workflow_run {workflow_run_id}"
            )

            # Extract call_id from workflow run gathered context as handshake does not contain it.
            workflow_run = await db_client.get_workflow_run(
                workflow_run_id, organization_id=organization_id
            )
            call_id = (workflow_run.gathered_context or {}).get("call_id") if workflow_run else None

            if not call_id:
                call_id = str(workflow_run_id)

            await run_pipeline_telephony(
                websocket,
                provider_name=self.PROVIDER_NAME,
                workflow_id=workflow_id,
                workflow_run_id=workflow_run_id,
                organization_id=organization_id,
                call_id=call_id,
                transport_kwargs={"call_id": call_id},
            )

        except WebSocketDisconnect as e:
            logger.info(
                f"[run {workflow_run_id}] Papi Voip WebSocket closed: "
                f"code={e.code}, reason={e.reason!r}"
            )
        except Exception as e:
            logger.error(f"Error in Papi Voip WebSocket handler: {e}")
            raise

    # ======== INBOUND CALL METHODS ========

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        user_agent = headers.get("user-agent", "").lower()
        return "papi" in user_agent or webhook_data.get("provider") == "papi_voip"

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        from_raw = webhook_data.get("from", "")
        to_raw = webhook_data.get("to", "")
        
        return NormalizedInboundData(
            provider=PapiVoipProvider.PROVIDER_NAME,
            call_id=webhook_data.get("call_id", ""),
            from_number=from_raw,
            to_number=to_raw,
            direction="inbound",
            call_status=webhook_data.get("status", "ringing"),
            account_id=webhook_data.get("instance_id", ""),
            raw_data=webhook_data,
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        return config_data.get("instance_id") == webhook_account_id

    async def verify_inbound_signature(
        self, url: str, webhook_data: Dict[str, Any], headers: Dict[str, str], body: str = ""
    ) -> bool:
        return True

    async def start_inbound_stream(
        self,
        *,
        websocket_url: str,
        workflow_run_id: int,
        normalized_data: NormalizedInboundData,
        backend_endpoint: str,
    ) -> Any:
        return {"action": "stream", "stream_url": websocket_url}

    async def transfer_call(self, *args, **kwargs) -> Dict[str, Any]:
        raise NotImplementedError

    def supports_transfers(self) -> bool:
        return False

    @staticmethod
    def generate_error_response(error_type: str, message: str) -> tuple:
        return {"error": message}, "application/json"

    async def validate_phone_number(self, address: str) -> ProviderSyncResult:
        return ProviderSyncResult(ok=True)
