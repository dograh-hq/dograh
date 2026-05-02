"""
Plivo implementation of the TelephonyProvider interface.
"""

import base64
import hashlib
import hmac
import json
import random
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse, urlunparse

import aiohttp
from fastapi import HTTPException
from loguru import logger

from api.db import db_client
from api.enums import WorkflowRunMode
from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    ProviderSyncResult,
    TelephonyProvider,
)
from api.utils.common import get_backend_endpoints
from api.utils.telephony_address import normalize_telephony_address

if TYPE_CHECKING:
    from fastapi import WebSocket


class PlivoProvider(TelephonyProvider):
    """
    Plivo implementation of TelephonyProvider.
    """

    PROVIDER_NAME = WorkflowRunMode.PLIVO.value
    WEBHOOK_ENDPOINT = "plivo-xml"

    def __init__(self, config: Dict[str, Any]):
        self.auth_id = config.get("auth_id")
        self.auth_token = config.get("auth_token")
        self.application_id = config.get("application_id")
        self.from_numbers = config.get("from_numbers", [])

        if isinstance(self.from_numbers, str):
            self.from_numbers = [self.from_numbers]

        self.base_url = f"https://api.plivo.com/v1/Account/{self.auth_id}"

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        if not self.validate_config():
            raise ValueError("Plivo provider not properly configured")

        endpoint = f"{self.base_url}/Call/"

        if from_number is None:
            from_number = random.choice(self.from_numbers)

        data = {
            "from": from_number.lstrip("+"),
            "to": to_number.lstrip("+"),
            "answer_url": webhook_url,
            "answer_method": "POST",
        }

        if workflow_run_id:
            backend_endpoint, _ = await get_backend_endpoints()
            data.update(
                {
                    "hangup_url": f"{backend_endpoint}/api/v1/telephony/plivo/hangup-callback/{workflow_run_id}",
                    "hangup_method": "POST",
                    "ring_url": f"{backend_endpoint}/api/v1/telephony/plivo/ring-callback/{workflow_run_id}",
                    "ring_method": "POST",
                }
            )

        data.update(kwargs)

        async with aiohttp.ClientSession() as session:
            auth = aiohttp.BasicAuth(self.auth_id, self.auth_token)
            async with session.post(endpoint, json=data, auth=auth) as response:
                response_text = await response.text()
                if response.status not in (200, 201, 202):
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"Failed to initiate Plivo call: {response_text}",
                    )

                response_data = json.loads(response_text)
                call_id = (
                    response_data.get("request_uuid")
                    or response_data.get("call_uuid")
                    or response_data.get("call_uuids", [None])[0]
                )

                if not call_id:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Plivo response missing call identifier: {response_data}",
                    )

                return CallInitiationResult(
                    call_id=call_id,
                    status=response_data.get("message", "queued"),
                    caller_number=from_number,
                    provider_metadata={"call_id": call_id},
                    raw_response=response_data,
                )

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        if not self.validate_config():
            raise ValueError("Plivo provider not properly configured")

        endpoint = f"{self.base_url}/Call/{call_id}/"

        async with aiohttp.ClientSession() as session:
            auth = aiohttp.BasicAuth(self.auth_id, self.auth_token)
            async with session.get(endpoint, auth=auth) as response:
                if response.status != 200:
                    error_data = await response.text()
                    raise Exception(f"Failed to get call status: {error_data}")

                return await response.json()

    async def get_available_phone_numbers(self) -> List[str]:
        return self.from_numbers

    def validate_config(self) -> bool:
        return bool(self.auth_id and self.auth_token and self.from_numbers)

    @staticmethod
    def _stringify_signature_value(value: Any) -> Any:
        if isinstance(value, bytes):
            return "".join(chr(x) for x in bytearray(value))
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, list):
            return [PlivoProvider._stringify_signature_value(item) for item in value]
        return value

    @staticmethod
    def _query_map(query: str) -> Dict[str, Any]:
        return {
            PlivoProvider._stringify_signature_value(
                key
            ): PlivoProvider._stringify_signature_value(value)
            for key, value in parse_qs(query, keep_blank_values=True).items()
        }

    @staticmethod
    def _sorted_query_string(params: Dict[str, Any]) -> str:
        parts: list[str] = []
        for key in sorted(params.keys()):
            value = params[key]
            if isinstance(value, list):
                normalized_values = sorted(
                    PlivoProvider._stringify_signature_value(value)
                )
                parts.append("&".join(f"{key}={item}" for item in normalized_values))
            else:
                parts.append(f"{key}={PlivoProvider._stringify_signature_value(value)}")
        return "&".join(parts)

    @staticmethod
    def _sorted_params_string(params: Dict[str, Any]) -> str:
        parts: list[str] = []
        for key in sorted(params.keys()):
            value = params[key]
            if isinstance(value, list):
                normalized_values = sorted(
                    PlivoProvider._stringify_signature_value(value)
                )
                parts.append("".join(f"{key}{item}" for item in normalized_values))
            elif isinstance(value, dict):
                parts.append(f"{key}{PlivoProvider._sorted_params_string(value)}")
            else:
                parts.append(f"{key}{PlivoProvider._stringify_signature_value(value)}")
        return "".join(parts)

    @staticmethod
    def _construct_get_url(
        uri: str, params: Dict[str, Any], empty_post_params: bool = True
    ) -> str:
        parsed_uri = urlparse(uri)
        base_url = urlunparse(
            (parsed_uri.scheme, parsed_uri.netloc, parsed_uri.path, "", "", "")
        )

        combined_params = dict(params)
        combined_params.update(PlivoProvider._query_map(parsed_uri.query))
        query_params = PlivoProvider._sorted_query_string(combined_params)

        if query_params or not empty_post_params:
            base_url = f"{base_url}?{query_params}"
        if query_params and not empty_post_params:
            base_url = f"{base_url}."
        return base_url

    @staticmethod
    def _construct_post_url(uri: str, params: Dict[str, Any]) -> str:
        base_url = PlivoProvider._construct_get_url(
            uri,
            {},
            empty_post_params=(len(params) == 0),
        )
        return f"{base_url}{PlivoProvider._sorted_params_string(params)}"

    async def verify_webhook_signature(
        self,
        url: str,
        params: Dict[str, Any],
        signature: str,
        nonce: str = "",
    ) -> bool:
        if not self.auth_token or not signature or not nonce:
            return False

        payload = f"{self._construct_post_url(url, params)}.{nonce}"
        computed = base64.b64encode(
            hmac.new(
                self.auth_token.encode("utf-8"),
                payload.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")

        candidates = [
            candidate.strip() for candidate in signature.split(",") if candidate
        ]
        return any(hmac.compare_digest(computed, candidate) for candidate in candidates)

    async def get_webhook_response(
        self, workflow_id: int, user_id: int, workflow_run_id: int
    ) -> str:
        _, wss_backend_endpoint = await get_backend_endpoints()

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">{wss_backend_endpoint}/api/v1/telephony/ws/{workflow_id}/{user_id}/{workflow_run_id}</Stream>
</Response>"""

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        endpoint = f"{self.base_url}/Call/{call_id}/"

        try:
            async with aiohttp.ClientSession() as session:
                auth = aiohttp.BasicAuth(self.auth_id, self.auth_token)
                async with session.get(endpoint, auth=auth) as response:
                    if response.status != 200:
                        error_data = await response.text()
                        logger.error(f"Failed to get Plivo call cost: {error_data}")
                        return {
                            "cost_usd": 0.0,
                            "duration": 0,
                            "status": "error",
                            "error": str(error_data),
                        }

                    call_data = await response.json()
                    total_amount = float(call_data.get("total_amount", 0) or 0)
                    duration = int(call_data.get("duration", 0) or 0)

                    return {
                        "cost_usd": total_amount,
                        "duration": duration,
                        "status": call_data.get("call_status", "unknown"),
                        "price_unit": "USD",
                        "raw_response": call_data,
                    }
        except Exception as e:
            logger.error(f"Exception fetching Plivo call cost: {e}")
            return {"cost_usd": 0.0, "duration": 0, "status": "error", "error": str(e)}

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        status_map = {
            "in-progress": "answered",
            "ringing": "ringing",
            "ring": "ringing",
            "completed": "completed",
            "hangup": "completed",
            "stopstream": "completed",
            "busy": "busy",
            "no-answer": "no-answer",
            "cancel": "canceled",
            "cancelled": "canceled",
            "timeout": "no-answer",
        }

        call_status = (data.get("CallStatus") or data.get("Event") or "").lower()
        return {
            "call_id": data.get("CallUUID", "") or data.get("RequestUUID", ""),
            "status": status_map.get(call_status, call_status),
            "from_number": data.get("From"),
            "to_number": data.get("To"),
            "direction": data.get("Direction"),
            "duration": data.get("Duration"),
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

        first_msg = await websocket.receive_text()
        start_msg = json.loads(first_msg)

        if start_msg.get("event") != "start":
            logger.error(f"Expected 'start' event, got: {start_msg.get('event')}")
            await websocket.close(code=4400, reason="Expected start event")
            return

        start_data = start_msg.get("start", {})
        stream_id = start_data.get("streamId") or start_msg.get("streamId")

        if not stream_id:
            logger.error(f"Missing streamId in start event: {start_msg}")
            await websocket.close(code=4400, reason="Missing streamId")
            return

        workflow_run = await db_client.get_workflow_run(workflow_run_id)
        call_id = None
        if workflow_run and workflow_run.gathered_context:
            call_id = workflow_run.gathered_context.get("call_id")

        if not call_id:
            call_id = start_data.get("callId") or start_data.get("callUUID")

        if not call_id:
            logger.error(f"Missing call ID for Plivo workflow run {workflow_run_id}")
            await websocket.close(code=4400, reason="Missing call ID")
            return

        await run_pipeline_telephony(
            websocket,
            provider_name=self.PROVIDER_NAME,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
            call_id=call_id,
            transport_kwargs={"stream_id": stream_id, "call_id": call_id},
        )

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        has_plivo_signature = (
            "x-plivo-signature-v3" in headers or "x-plivo-signature-ma-v3" in headers
        )
        return has_plivo_signature and "CallUUID" in webhook_data

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        from_raw = webhook_data.get("From", "")
        to_raw = webhook_data.get("To", "")
        return NormalizedInboundData(
            provider=PlivoProvider.PROVIDER_NAME,
            call_id=webhook_data.get("CallUUID", "")
            or webhook_data.get("RequestUUID", ""),
            from_number=normalize_telephony_address(from_raw).canonical
            if from_raw
            else "",
            to_number=normalize_telephony_address(to_raw).canonical if to_raw else "",
            direction=webhook_data.get("Direction", ""),
            call_status=webhook_data.get("CallStatus", ""),
            account_id=webhook_data.get("AuthID") or webhook_data.get("ParentAuthID"),
            raw_data=webhook_data,
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        if webhook_account_id:
            return config_data.get("auth_id") == webhook_account_id
        # AuthID is not always present in Plivo webhooks (undocumented field).
        # Fall back to checking that the org has a Plivo config at all.
        logger.warning(
            "Plivo webhook missing AuthID/ParentAuthID - "
            "falling back to config existence check"
        )
        return bool(config_data.get("auth_id"))

    async def verify_inbound_signature(
        self,
        url: str,
        webhook_data: Dict[str, Any],
        headers: Dict[str, str],
        body: str = "",
    ) -> bool:
        signature = headers.get("x-plivo-signature-v3") or headers.get(
            "x-plivo-signature-ma-v3", ""
        )
        nonce = headers.get("x-plivo-signature-v3-nonce", "")
        if not signature:
            # Plivo always signs its webhooks; missing header means the
            # request didn't come from Plivo (or was tampered with).
            logger.warning("Inbound Plivo webhook missing X-Plivo-Signature-V3")
            return False
        return await self.verify_webhook_signature(url, webhook_data, signature, nonce)

    async def configure_inbound(
        self, address: str, webhook_url: Optional[str]
    ) -> ProviderSyncResult:
        """Update the answer_url on the configured Plivo Application.

        Plivo numbers don't carry an answer_url directly — the URL lives on a
        Plivo Application, and a number is linked to one app via ``app_id``.
        Every call to this method updates the answer_url on
        ``self.application_id``, regardless of which ``address`` triggered the
        sync. ``address`` is informational. Linking the number to
        ``self.application_id`` (in the Plivo console, or via the Account
        Phone Number API) is the operator's responsibility — we only update
        the application's webhook here.

        Clearing (``webhook_url=None``) is a no-op on Plivo's side: the URL
        is shared across every number linked to this application, so
        unsetting it for one number would silently break inbound for the
        rest. The DB-level disconnect is sufficient — inbound calls without
        a matching workflow are rejected by the backend.
        """
        if webhook_url is None:
            logger.info(
                f"Plivo configure_inbound clear for {address}: skipping "
                f"application update (answer_url is shared across all numbers "
                f"on application {self.application_id})"
            )
            return ProviderSyncResult(ok=True)

        if not self.validate_config():
            return ProviderSyncResult(
                ok=False, message="Plivo provider not properly configured"
            )

        if not self.application_id:
            return ProviderSyncResult(
                ok=False,
                message=(
                    "Plivo application_id is not configured. Set it in the "
                    "telephony configuration so inbound webhooks can be "
                    "synced to the right Application."
                ),
            )

        app_endpoint = f"{self.base_url}/Application/{self.application_id}/"
        data = {
            "answer_url": webhook_url,
            "answer_method": "POST",
        }
        auth = aiohttp.BasicAuth(self.auth_id, self.auth_token)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(app_endpoint, json=data, auth=auth) as response:
                    if response.status not in (200, 202):
                        body = await response.text()
                        logger.error(
                            f"Plivo application update failed for "
                            f"{self.application_id}: {response.status} {body}"
                        )
                        return ProviderSyncResult(
                            ok=False,
                            message=f"Plivo API {response.status}: {body}",
                        )
        except Exception as e:
            logger.error(
                f"Exception updating Plivo application {self.application_id}: {e}"
            )
            return ProviderSyncResult(ok=False, message=f"Plivo update failed: {e}")

        logger.info(
            f"Plivo answer_url set on application {self.application_id} "
            f"(triggered by address {address})"
        )
        return ProviderSyncResult(ok=True)

    async def start_inbound_stream(
        self,
        *,
        websocket_url: str,
        workflow_run_id: int,
        normalized_data,
        backend_endpoint: str,
    ):
        from fastapi import Response

        hangup_callback_attr = ""
        if workflow_run_id:
            hangup_url = f"{backend_endpoint}/api/v1/telephony/plivo/hangup-callback/{workflow_run_id}"
            hangup_callback_attr = (
                f' statusCallbackUrl="{hangup_url}" statusCallbackMethod="POST"'
            )

        plivo_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000"{hangup_callback_attr}>{websocket_url}</Stream>
</Response>"""
        return Response(content=plivo_xml, media_type="application/xml")

    @staticmethod
    def generate_error_response(error_type: str, message: str) -> tuple:
        from fastapi import Response

        plivo_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Speak>Sorry, there was an error processing your call. {message}</Speak>
    <Hangup/>
</Response>"""
        return Response(content=plivo_xml, media_type="application/xml")

    @staticmethod
    def generate_validation_error_response(error_type) -> tuple:
        from fastapi import Response

        from api.errors.telephony_errors import TELEPHONY_ERROR_MESSAGES, TelephonyError

        message = TELEPHONY_ERROR_MESSAGES.get(
            error_type, TELEPHONY_ERROR_MESSAGES[TelephonyError.GENERAL_AUTH_FAILED]
        )

        plivo_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Speak>{message}</Speak>
    <Hangup/>
</Response>"""
        return Response(content=plivo_xml, media_type="application/xml")

    async def transfer_call(
        self,
        destination: str,
        transfer_id: str,
        conference_name: str,
        timeout: int = 30,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        raise NotImplementedError("Plivo provider does not support call transfers")

    def supports_transfers(self) -> bool:
        return False
