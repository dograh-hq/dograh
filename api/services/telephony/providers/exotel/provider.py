"""Exotel implementation of the TelephonyProvider interface.

Outbound uses Connect Voice AI:
https://docs.exotel.com/exotel-agentstream/connect-voice-ai-api
"""

import base64
import json
import random
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import aiohttp
from fastapi import HTTPException, Response
from loguru import logger

from api.enums import TelephonyCallStatus, WorkflowRunMode
from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    ProviderPhoneNumberLookupError,
    ProviderSyncResult,
    TelephonyProvider,
)
from api.utils.common import get_backend_endpoints
from api.utils.telephony_address import normalize_telephony_address

if TYPE_CHECKING:
    from fastapi import WebSocket

DEFAULT_API_BASE_URL = "https://api.in.exotel.com"


class ExotelProvider(TelephonyProvider):
    PROVIDER_NAME = WorkflowRunMode.EXOTEL.value
    WEBHOOK_ENDPOINT = "exotel"

    def __init__(self, config: Dict[str, Any]):
        self.account_sid = config.get("account_sid")
        self.api_key = config.get("api_key")
        self.api_token = config.get("api_token")
        self.api_base_url = (config.get("api_base_url") or DEFAULT_API_BASE_URL).rstrip(
            "/"
        )
        self.from_numbers = config.get("from_numbers", [])
        if isinstance(self.from_numbers, str):
            self.from_numbers = [self.from_numbers]

    def _auth(self) -> aiohttp.BasicAuth:
        return aiohttp.BasicAuth(self.api_key, self.api_token)

    def _calls_connect_url(self) -> str:
        return f"{self.api_base_url}/v1/Accounts/{self.account_sid}/Calls/connect.json"

    def _call_url(self, call_id: str) -> str:
        return (
            f"{self.api_base_url}/v1/Accounts/{self.account_sid}/Calls/{call_id}.json"
        )

    @staticmethod
    def _exotel_dial_number(number: str) -> str:
        """Map E.164 to Exotel's usual 0-prefixed national form for India.

        Working Connect examples use CallerId/From like ``07314852338``, not
        ``+917314852338``. Other regions keep the input as-is.
        """
        n = (number or "").strip()
        if n.startswith("+91") and len(n) >= 12:
            return "0" + n[3:]
        if n.startswith("91") and len(n) == 12 and n.isdigit():
            return "0" + n[2:]
        return n

    @staticmethod
    def _number_match_keys(raw: str, country_hint: Optional[str] = None) -> set[str]:
        """Build comparable key set across E.164 and Exotel national formats."""
        keys: set[str] = set()
        text = (raw or "").strip()
        if not text:
            return keys
        keys.add(text)
        keys.add(text.lstrip("+"))
        try:
            norm = normalize_telephony_address(text, country_hint)
            keys.add(norm.canonical)
            keys.add(norm.canonical.lstrip("+"))
            digits = norm.canonical.lstrip("+")
            if digits.startswith("91") and len(digits) >= 12:
                national = digits[2:].lstrip("0")
                keys.add(national)
                keys.add("0" + national)
        except Exception:
            pass
        return {k for k in keys if k}

    @staticmethod
    def _iter_incoming_phone_entries(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Unwrap Exotel's nested IncomingPhoneNumber list payload."""
        numbers = (
            data.get("IncomingPhoneNumbers")
            or data.get("PhoneNumbers")
            or []
        )
        if isinstance(numbers, dict):
            numbers = [numbers]
        out: List[Dict[str, Any]] = []
        for entry in numbers:
            if isinstance(entry, dict) and "IncomingPhoneNumber" in entry:
                nested = entry.get("IncomingPhoneNumber")
                if isinstance(nested, dict):
                    out.append(nested)
                continue
            if isinstance(entry, dict):
                out.append(entry)
        return out

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        """Initiate an outbound call via Exotel Connect Voice AI.

        Note: webhook_url is ignored. Exotel attaches StreamUrl on dial,
        similar to Cloudonix embedding stream markup in the API call.
        """
        if not self.validate_config():
            raise ValueError("Exotel provider not properly configured")

        workflow_id = kwargs["workflow_id"]
        organization_id = kwargs["organization_id"]

        if from_number is None:
            if not self.from_numbers:
                raise ValueError(
                    "No phone numbers configured for Exotel. "
                    "Add at least one ExoPhone as CallerId."
                )
            from_number = random.choice(self.from_numbers)

        to_number = self._exotel_dial_number(to_number)
        from_number = self._exotel_dial_number(from_number)

        backend_endpoint, wss_backend_endpoint = await get_backend_endpoints()
        stream_url = (
            f"{wss_backend_endpoint}/api/v1/telephony/ws/"
            f"{workflow_id}/{organization_id}/{workflow_run_id}"
        )

        form: Dict[str, Any] = {
            "From": to_number,
            "CallerId": from_number,
            "StreamUrl": stream_url,
            "StreamType": "bidirectional",
        }
        if workflow_run_id:
            form["StatusCallback"] = (
                f"{backend_endpoint}/api/v1/telephony/exotel/"
                f"status-callback/{workflow_run_id}"
            )
            form["StatusCallbackEvents[]"] = "terminal"

        logger.info(
            f"[Exotel] Initiating call to={to_number} from={from_number} "
            f"run={workflow_run_id}"
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._calls_connect_url(), data=form, auth=self._auth()
            ) as response:
                body_text = await response.text()
                if response.status not in (200, 201):
                    logger.error(
                        f"[Exotel] Calls/connect failed HTTP {response.status}: "
                        f"{body_text}"
                    )
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"Exotel Calls/connect failed: {body_text}",
                    )
                try:
                    response_data = json.loads(body_text)
                except json.JSONDecodeError as e:
                    raise HTTPException(
                        status_code=502,
                        detail=f"Exotel returned non-JSON response: {body_text[:200]}",
                    ) from e

        call = response_data.get("Call") or response_data
        call_id = str(call.get("Sid") or "")
        if not call_id:
            raise HTTPException(
                status_code=502,
                detail=f"Exotel response missing Call Sid: {response_data}",
            )

        return CallInitiationResult(
            call_id=call_id,
            status=str(call.get("Status") or "queued"),
            caller_number=from_number,
            provider_metadata={"call_id": call_id},
            raw_response=response_data,
        )

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        if not self.validate_config():
            raise ValueError("Exotel provider not properly configured")

        async with aiohttp.ClientSession() as session:
            async with session.get(
                self._call_url(call_id), auth=self._auth()
            ) as response:
                if response.status != 200:
                    error_data = await response.text()
                    raise Exception(f"Failed to get Exotel call status: {error_data}")
                return await response.json()

    async def get_available_phone_numbers(self) -> List[str]:
        return list(self.from_numbers)

    def validate_config(self) -> bool:
        return bool(self.account_sid and self.api_key and self.api_token)

    async def validate_phone_number(self, address: str) -> ProviderSyncResult:
        """Verify ownership via Exotel IncomingPhoneNumbers (Twilio-compatible)."""
        # Exotel India numbers are often stored as 0-prefixed national
        # (e.g. 07314852338). Hint IN so bare local forms normalize correctly.
        country_hint = "IN" if address.strip().startswith("0") else None
        normalized = normalize_telephony_address(address, country_hint)
        if normalized.address_type != "pstn":
            return ProviderSyncResult(ok=True)
        if not self.validate_config():
            raise ProviderPhoneNumberLookupError(
                "Exotel account SID, API key, and API token are required to "
                "validate phone-number ownership"
            )

        wanted = self._number_match_keys(address, country_hint)
        wanted |= self._number_match_keys(normalized.canonical, "IN")
        # Query candidates Exotel may accept as PhoneNumber filters.
        candidates = sorted(wanted)
        endpoint = (
            f"{self.api_base_url}/v1/Accounts/{self.account_sid}/"
            f"IncomingPhoneNumbers.json"
        )
        try:
            async with aiohttp.ClientSession() as session:
                # First try filtered lookups, then fall back to full list
                # (Exotel filter often ignores E.164).
                query_plans: List[Optional[Dict[str, str]]] = [
                    {"PhoneNumber": phone} for phone in candidates
                ]
                query_plans.append(None)

                for params in query_plans:
                    async with session.get(
                        endpoint, params=params, auth=self._auth()
                    ) as response:
                        if response.status == 404:
                            continue
                        if response.status != 200:
                            body = await response.text()
                            raise ProviderPhoneNumberLookupError(
                                f"Exotel API {response.status}: {body}"
                            )
                        data = await response.json()
                        for entry in self._iter_incoming_phone_entries(data):
                            raw = str(
                                entry.get("PhoneNumber")
                                or entry.get("FriendlyName")
                                or ""
                            )
                            if not raw:
                                continue
                            owned = self._number_match_keys(raw, "IN")
                            if wanted & owned:
                                return ProviderSyncResult(ok=True)
                return ProviderSyncResult(
                    ok=False,
                    message=(
                        f"Phone number {normalized.canonical} is not owned by "
                        f"this Exotel account ({self.account_sid}). Add it in "
                        "the Exotel dashboard first."
                    ),
                )
        except ProviderPhoneNumberLookupError:
            raise
        except Exception as e:
            raise ProviderPhoneNumberLookupError(
                f"Exotel phone-number lookup failed: {e}"
            ) from e

    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        # Unused: Connect Voice AI attaches StreamUrl at dial time.
        logger.warning(
            "verify_webhook_signature called for Exotel - unexpected for Connect API"
        )
        return False

    async def get_webhook_response(
        self, workflow_id: int, organization_id: int, workflow_run_id: int
    ) -> str:
        return ""

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        try:
            call_data = await self.get_call_status(call_id)
            call = call_data.get("Call") or call_data
            price = call.get("Price") or "0"
            duration = call.get("Duration") or 0
            return {
                "cost_usd": abs(float(price)) if price else 0.0,
                "duration": int(duration) if duration else 0,
                "status": call.get("Status") or "unknown",
                "raw_response": call_data,
            }
        except Exception as e:
            logger.error(f"Exception fetching Exotel call cost: {e}")
            return {
                "cost_usd": 0.0,
                "duration": 0,
                "status": "error",
                "error": str(e),
            }

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        status_raw = data.get("Status") or data.get("CallStatus") or ""
        return {
            "call_id": data.get("CallSid") or data.get("Sid") or "",
            "status": TelephonyCallStatus.from_raw(status_raw) or status_raw,
            "from_number": data.get("From") or data.get("CallFrom"),
            "to_number": data.get("To") or data.get("CallTo"),
            "direction": data.get("Direction"),
            "duration": data.get("Duration") or data.get("ConversationDuration"),
            "extra": data,
        }

    async def handle_websocket(
        self,
        websocket: "WebSocket",
        workflow_id: int,
        organization_id: int,
        workflow_run_id: int,
    ) -> None:
        from api.services.pipecat.run_pipeline import run_pipeline_telephony

        try:
            first_msg = await websocket.receive_text()
            msg = json.loads(first_msg)
            if msg.get("event") != "connected":
                logger.error(f"Expected 'connected' event, got: {msg.get('event')}")
                await websocket.close(code=4400, reason="Expected connected event")
                return

            start_msg = json.loads(await websocket.receive_text())
            if start_msg.get("event") != "start":
                logger.error("Expected 'start' event second")
                await websocket.close(code=4400, reason="Expected start event")
                return

            start = start_msg.get("start")
            if not isinstance(start, dict):
                logger.error("Exotel start message missing start object")
                await websocket.close(code=4400, reason="Missing start metadata")
                return

            # Exotel Voicebot docs use snake_case; some Twilio-shaped paths use
            # camelCase. Accept both, including top-level stream_sid.
            stream_sid = (
                start.get("streamSid")
                or start.get("stream_sid")
                or start_msg.get("streamSid")
                or start_msg.get("stream_sid")
            )
            call_sid = (
                start.get("callSid")
                or start.get("call_sid")
                or start_msg.get("callSid")
                or start_msg.get("call_sid")
            )
            if not stream_sid or not call_sid:
                logger.error(
                    "Missing streamSid/callSid in Exotel start message: "
                    f"{start_msg}"
                )
                await websocket.close(code=4400, reason="Missing stream identifiers")
                return

            logger.info(
                f"Exotel WebSocket connected for workflow_run {workflow_run_id} "
                f"stream_sid={stream_sid} call_sid={call_sid}"
            )

            await run_pipeline_telephony(
                websocket,
                provider_name=self.PROVIDER_NAME,
                workflow_id=workflow_id,
                workflow_run_id=workflow_run_id,
                organization_id=organization_id,
                call_id=str(call_sid),
                transport_kwargs={
                    "stream_sid": str(stream_sid),
                    "call_sid": str(call_sid),
                },
            )
        except Exception as e:
            logger.error(f"Error in Exotel WebSocket handler: {e}")
            raise

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        normalized = {k.lower(): v for k, v in headers.items()}
        user_agent = (normalized.get("user-agent") or "").lower()
        if "exotel" in user_agent:
            return True

        account_sid = str(
            webhook_data.get("AccountSid") or webhook_data.get("accountSid") or ""
        )
        call_sid = webhook_data.get("CallSid") or webhook_data.get("callSid")
        from_number = webhook_data.get("From") or webhook_data.get("CallFrom")
        if not (account_sid and call_sid and from_number):
            return False
        # Exclude Twilio (AC…) and Cloudonix (*.cloudonix.net)
        if account_sid.startswith("AC") and "." not in account_sid:
            return False
        if account_sid.endswith(".cloudonix.net"):
            return False
        return True

    @staticmethod
    def _india_country_hint(number: str) -> Optional[str]:
        """Exotel India often sends 0-prefixed national numbers (e.g. 0731…).

        Without an IN hint, normalize_telephony_address turns those into a
        junk E.164 like +0731… and inbound route lookup misses the stored
        +91… phone row.
        """
        digits = "".join(ch for ch in (number or "").strip() if ch.isdigit())
        if digits.startswith("0") and 10 <= len(digits) <= 11:
            return "IN"
        return None

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        call_id = (
            webhook_data.get("CallSid")
            or webhook_data.get("callSid")
            or webhook_data.get("Sid")
            or ""
        )
        account_id = webhook_data.get("AccountSid") or webhook_data.get("accountSid")
        from_number = (
            webhook_data.get("From")
            or webhook_data.get("CallFrom")
            or webhook_data.get("from")
            or ""
        )
        to_number = (
            webhook_data.get("To")
            or webhook_data.get("CallTo")
            or webhook_data.get("to")
            or ""
        )
        direction = (
            webhook_data.get("Direction")
            or webhook_data.get("direction")
            or "inbound"
        ).lower()
        if direction in {"incoming", "inbound"}:
            direction = "inbound"

        call_status = (
            webhook_data.get("CallStatus")
            or webhook_data.get("Status")
            or webhook_data.get("callStatus")
            or ""
        )

        return NormalizedInboundData(
            provider=ExotelProvider.PROVIDER_NAME,
            call_id=str(call_id),
            from_number=str(from_number),
            to_number=str(to_number),
            direction=direction,
            call_status=str(call_status),
            account_id=str(account_id) if account_id else None,
            to_country=ExotelProvider._india_country_hint(str(to_number)),
            raw_data=webhook_data,
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        if not webhook_account_id:
            return False
        stored = config_data.get("account_sid")
        return bool(stored) and stored == webhook_account_id

    async def verify_inbound_signature(
        self,
        url: str,
        webhook_data: Dict[str, Any],
        headers: Dict[str, str],
        body: str = "",
    ) -> bool:
        """Fail closed: Exotel does not document an HMAC inbound signature.

        Streaming docs support Basic Auth (api_key:api_token). Require
        Authorization Basic matching stored credentials.
        """
        normalized = {k.lower(): v for k, v in headers.items()}
        auth = normalized.get("authorization", "")
        if not auth.lower().startswith("basic "):
            logger.warning(
                "Exotel inbound webhook missing Authorization Basic; rejecting"
            )
            return False
        if not self.api_key or not self.api_token:
            logger.warning("Exotel credentials missing for inbound Basic Auth check")
            return False
        try:
            decoded = base64.b64decode(auth.split(" ", 1)[1].strip()).decode("utf-8")
            user, _, password = decoded.partition(":")
        except Exception as e:
            logger.warning(f"Exotel inbound Basic Auth decode failed: {e}")
            return False
        ok = user == self.api_key and password == self.api_token
        if not ok:
            logger.warning("Exotel inbound Basic Auth mismatch")
        return ok

    async def start_inbound_stream(
        self,
        *,
        websocket_url: str,
        workflow_run_id: int,
        normalized_data,
        backend_endpoint: str,
    ):
        status_attr = ""
        if workflow_run_id:
            status_url = (
                f"{backend_endpoint}/api/v1/telephony/exotel/"
                f"status-callback/{workflow_run_id}"
            )
            status_attr = f' statusCallback="{status_url}"'

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{websocket_url}"{status_attr}></Stream>
    </Connect>
    <Pause length="40"/>
</Response>"""
        return Response(content=xml, media_type="application/xml")

    @staticmethod
    def generate_validation_error_response(error_type) -> Response:
        """TwiML/ExoML hangup used when inbound route/auth validation fails."""
        from api.errors.telephony_errors import TELEPHONY_ERROR_MESSAGES, TelephonyError

        message = TELEPHONY_ERROR_MESSAGES.get(
            error_type, TELEPHONY_ERROR_MESSAGES[TelephonyError.GENERAL_AUTH_FAILED]
        )
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>{message}</Say>
    <Hangup/>
</Response>"""
        return Response(content=xml, media_type="application/xml")

    @staticmethod
    def generate_error_response(error_type: str, message: str):
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Sorry, there was an error processing your call. {message}</Say>
    <Hangup/>
</Response>"""
        return Response(content=xml, media_type="application/xml")

    async def transfer_call(
        self,
        destination: str,
        transfer_id: str,
        conference_name: str,
        timeout: int = 30,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        raise NotImplementedError("Exotel provider does not support call transfers")

    def supports_transfers(self) -> bool:
        return False
