"""Native TryVox implementation of Dograh's telephony provider contract."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from urllib.parse import quote, urlencode, urlparse

import aiohttp
from fastapi import HTTPException
from loguru import logger

from api.enums import TelephonyCallStatus
from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    ProviderPhoneNumberLookupError,
    ProviderSyncResult,
    TelephonyProvider,
)
from api.utils.common import get_backend_endpoints
from api.utils.telephony_address import normalize_telephony_address

from .security import tryvox_security

if TYPE_CHECKING:
    from fastapi import WebSocket


class TryVoxProvider(TelephonyProvider):
    """TryVox Voice API, VoxML, webhook, and media-stream integration."""

    PROVIDER_NAME = "tryvox"
    WEBHOOK_ENDPOINT = "tryvox/answer"
    REQUIRES_AUTHENTICATED_WEBSOCKET = True
    SIGNATURE_MAX_AGE_SECONDS = 300

    @staticmethod
    def _voxml_hangup_response(message: str) -> str:
        return json.dumps(
            {
                "voxml_version": "1.0",
                "instructions": [
                    {"verb": "Say", "text": message},
                    {"verb": "Hangup"},
                ],
            }
        )

    def __init__(self, config: dict[str, Any]):
        self.auth_id = config.get("auth_id", "")
        self.auth_token = config.get("auth_token", "")
        self.webhook_secret = config.get("webhook_secret", "")
        self.application_id = config.get("application_id")
        self.api_base_url = (
            config.get("api_base_url") or "https://api.tryvox.io"
        ).rstrip("/")
        self.from_numbers = config.get("from_numbers", [])
        self.default_from_number = config.get("default_from_number")
        if isinstance(self.from_numbers, str):
            self.from_numbers = [self.from_numbers]

    def _auth(self) -> aiohttp.BasicAuth:
        return aiohttp.BasicAuth(self.auth_id, self.auth_token)

    def _call_url(self, call_id: str | None = None) -> str:
        url = f"{self.api_base_url}/v1/voice/accounts/{self.auth_id}/calls"
        return f"{url}/{call_id}" if call_id else url

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: int | None = None,
        from_number: str | None = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        if not self.validate_config():
            raise ValueError("TryVox provider not properly configured")

        selected_from = self.select_from_number(from_number)
        if not selected_from:
            raise ValueError("TryVox outbound call requires an account phone number")

        correlation_token = None
        if workflow_run_id is not None:
            correlation_token = await tryvox_security.issue_call_correlation(
                workflow_run_id
            )
            separator = "&" if "?" in webhook_url else "?"
            webhook_url = (
                f"{webhook_url}{separator}"
                f"correlation_token={quote(correlation_token, safe='')}"
            )

        data: dict[str, Any] = {
            "from": selected_from,
            "to": to_number,
            "answer_url": webhook_url,
            "answer_method": "POST",
            "webhook_secret": self.webhook_secret,
        }
        if workflow_run_id is not None:
            backend_endpoint, _ = await get_backend_endpoints()
            status_query = urlencode({"correlation_token": correlation_token})
            data.update(
                {
                    "status_callback_url": (
                        f"{backend_endpoint}/api/v1/telephony/"
                        f"tryvox/status/{workflow_run_id}?{status_query}"
                    ),
                    "status_callback_method": "POST",
                }
            )

        async with (
            aiohttp.ClientSession() as session,
            session.post(self._call_url(), json=data, auth=self._auth()) as response,
        ):
            response_data = await self._json_response(response)
            if response.status != 201:
                raise HTTPException(
                    status_code=response.status,
                    detail=f"TryVox call initiation failed: {response_data}",
                )

        call_data = response_data.get("data", response_data)
        # Answer and status callbacks carry TryVox's call UUID (``call_uuid`` /
        # ``CallUUID``), which is not guaranteed to equal the ``request_uuid``
        # from this REST response. Persisting the wrong one into
        # gathered_context would make `_assert_call_matches` permanently
        # reject the real callback (it only lets the *first* callback claim
        # an unset call ID). So only pre-bind the callback-compatible ID; if
        # the response omitted it, leave gathered_context.call_id unset and
        # let the first verified Answer/status callback claim it instead.
        callback_call_id = call_data.get("call_uuid") or call_data.get("CallUUID")
        call_id = callback_call_id or call_data.get("request_uuid")
        if not call_id:
            raise HTTPException(
                status_code=502,
                detail="TryVox call response did not include a call ID",
            )
        if correlation_token is not None:
            try:
                activated = await tryvox_security.activate_call_correlation(
                    workflow_run_id, correlation_token
                )
                if not activated:
                    logger.warning(
                        f"[run {workflow_run_id}] TryVox callback correlation was "
                        "already retired before call initiation completed"
                    )
            except Exception:
                # The provider already accepted the call. Propagating a Redis
                # failure here could make the caller retry and place a duplicate.
                logger.exception(
                    f"[run {workflow_run_id}] Failed to activate TryVox "
                    "callback correlation after call initiation"
                )
        return CallInitiationResult(
            call_id=call_id,
            status=call_data.get("status", "queued"),
            caller_number=selected_from,
            provider_metadata=(
                {"call_id": callback_call_id} if callback_call_id else {}
            ),
            raw_response=response_data,
        )

    async def get_call_status(self, call_id: str) -> dict[str, Any]:
        if not self.validate_config():
            raise ValueError("TryVox provider not properly configured")
        async with (
            aiohttp.ClientSession() as session,
            session.get(self._call_url(call_id), auth=self._auth()) as response,
        ):
            data = await self._json_response(response)
            if response.status != 200:
                raise HTTPException(
                    status_code=response.status,
                    detail=f"TryVox call lookup failed: {data}",
                )
            return data.get("data", data)

    async def get_available_phone_numbers(self) -> list[str]:
        return self.from_numbers

    async def validate_phone_number(self, address: str) -> ProviderSyncResult:
        """Verify PSTN ownership through TryVox's account Numbers resource."""
        normalized = normalize_telephony_address(address)
        if normalized.address_type != "pstn":
            return ProviderSyncResult(ok=True)
        if not (self.auth_id and self.auth_token):
            raise ProviderPhoneNumberLookupError(
                "TryVox auth ID and auth token are required to validate "
                "phone-number ownership"
            )

        encoded_address = quote(normalized.canonical, safe="+")
        endpoint = (
            f"{self.api_base_url}/v1/account/{self.auth_id}/numbers/"
            f"{encoded_address}"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(endpoint, auth=self._auth()) as response:
                    if response.status == 200:
                        return ProviderSyncResult(ok=True)
                    if response.status == 404:
                        return ProviderSyncResult(
                            ok=False,
                            message=(
                                f"Phone number {normalized.canonical} is not "
                                f"owned by this TryVox account ({self.auth_id}). "
                                "Add it in the TryVox console first."
                            ),
                        )
                    body = await response.text()
                    raise ProviderPhoneNumberLookupError(
                        f"TryVox API {response.status}: {body}"
                    )
        except ProviderPhoneNumberLookupError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ProviderPhoneNumberLookupError(
                f"TryVox phone-number lookup failed: {exc}"
            ) from exc

    def validate_config(self) -> bool:
        return bool(
            self.auth_id
            and self.auth_token
            and self.webhook_secret
            and self.from_numbers
        )

    async def verify_webhook_signature(
        self, url: str, params: dict[str, Any], signature: str
    ) -> bool:
        timestamp = self._signature_timestamp(signature)
        body = json.dumps(params, separators=(",", ":"), ensure_ascii=False)
        return self._verify_raw_signature(body, timestamp, signature)

    async def get_webhook_response(
        self, workflow_id: int, organization_id: int, workflow_run_id: int
    ) -> str:
        _, websocket_endpoint = await get_backend_endpoints()
        stream_token = await tryvox_security.issue_stream_token(
            workflow_id, organization_id, workflow_run_id
        )
        if stream_token is None:
            return self._voxml_hangup_response(
                "This call's media stream has already been connected."
            )
        stream_query = urlencode({"token": stream_token})
        return json.dumps(
            {
                "voxml_version": "1.0",
                "instructions": [
                    {
                        "verb": "Stream",
                        "url": (
                            f"{websocket_endpoint}/api/v1/telephony/tryvox/ws/"
                            f"{workflow_id}/{organization_id}/{workflow_run_id}"
                            f"?{stream_query}"
                        ),
                        "track": "inbound_track",
                        "parameters": {
                            "provider": self.PROVIDER_NAME,
                            "workflow_run_id": str(workflow_run_id),
                        },
                    }
                ],
            }
        )

    async def get_call_cost(self, call_id: str) -> dict[str, Any]:
        try:
            call = await self.get_call_status(call_id)
            return {
                "cost_usd": 0.0,
                "duration": int(call.get("billsec") or call.get("duration") or 0),
                "status": call.get("status", "unknown"),
                "raw_response": call,
            }
        except (
            aiohttp.ClientError,
            HTTPException,
            TimeoutError,
            TypeError,
            ValueError,
        ) as exc:
            logger.error(f"TryVox call status lookup failed: {exc}")
            return {
                "cost_usd": 0.0,
                "duration": 0,
                "status": "error",
                "error": str(exc),
            }

    def parse_status_callback(self, data: dict[str, Any]) -> dict[str, Any]:
        raw_status = data.get("Status") or data.get("status") or ""
        raw_duration = data.get("Duration") or data.get("duration")
        status_map = {
            "queued": TelephonyCallStatus.INITIATED,
            "hangup": TelephonyCallStatus.COMPLETED,
            "completed": TelephonyCallStatus.COMPLETED,
            "cancelled": TelephonyCallStatus.CANCELED,
            "canceled": TelephonyCallStatus.CANCELED,
        }
        normalized_status = status_map.get(str(raw_status).lower())
        return {
            "call_id": data.get("CallUUID") or data.get("call_uuid") or "",
            "status": normalized_status
            or TelephonyCallStatus.from_raw(raw_status)
            or raw_status,
            "from_number": data.get("From") or data.get("from"),
            "to_number": data.get("To") or data.get("to"),
            "direction": data.get("Direction") or data.get("direction"),
            "duration": str(raw_duration) if raw_duration is not None else None,
            "extra": data,
        }

    async def handle_websocket(
        self,
        websocket: WebSocket,
        workflow_id: int,
        organization_id: int,
        workflow_run_id: int,
        on_media_ready: Callable[[], Awaitable[bool]] | None = None,
        on_media_startup_failure: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        from api.db import db_client
        from api.services.pipecat.run_pipeline import run_pipeline_telephony

        workflow_run = await db_client.get_workflow_run(
            workflow_run_id, organization_id=organization_id
        )
        if not workflow_run:
            await websocket.close(code=4404, reason="Workflow run not found")
            return

        call_id = (workflow_run.gathered_context or {}).get("call_id")
        if not call_id:
            await websocket.close(code=4400, reason="Missing TryVox call ID")
            return

        try:
            metadata_text = await asyncio.wait_for(
                websocket.receive_text(), timeout=10.0
            )
            metadata = json.loads(metadata_text)
        except TimeoutError:
            await websocket.close(code=4408, reason="Stream metadata timeout")
            return
        except (json.JSONDecodeError, RuntimeError):
            await websocket.close(code=4400, reason="Invalid Stream metadata")
            return

        if not isinstance(metadata, dict) or str(
            metadata.get("workflow_run_id")
        ) != str(workflow_run_id):
            await websocket.close(code=4403, reason="Stream metadata mismatch")
            return

        await run_pipeline_telephony(
            websocket,
            provider_name=self.PROVIDER_NAME,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            organization_id=organization_id,
            call_id=call_id,
            transport_kwargs={"call_id": call_id},
            on_ready=on_media_ready,
            on_startup_failure=on_media_startup_failure,
        )

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: dict[str, Any], headers: dict[str, str]
    ) -> bool:
        normalized_headers = {key.lower(): value for key, value in headers.items()}
        user_agent = normalized_headers.get("user-agent", "").lower()
        return (
            "tryvox" in user_agent or "x-tryvox-signature" in normalized_headers
        ) and bool(webhook_data.get("call_uuid") or webhook_data.get("CallUUID"))

    @staticmethod
    def parse_inbound_webhook(webhook_data: dict[str, Any]) -> NormalizedInboundData:
        from_raw = webhook_data.get("from") or webhook_data.get("From") or ""
        to_raw = webhook_data.get("to") or webhook_data.get("To") or ""
        return NormalizedInboundData(
            provider=TryVoxProvider.PROVIDER_NAME,
            call_id=webhook_data.get("call_uuid") or webhook_data.get("CallUUID") or "",
            from_number=(
                normalize_telephony_address(from_raw).canonical if from_raw else ""
            ),
            to_number=normalize_telephony_address(to_raw).canonical if to_raw else "",
            direction=webhook_data.get("direction")
            or webhook_data.get("Direction")
            or "",
            call_status=webhook_data.get("status") or webhook_data.get("Status") or "",
            account_id=webhook_data.get("account_id")
            or webhook_data.get("AccountID")
            or webhook_data.get("AccountId"),
            raw_data=webhook_data,
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        return (
            bool(webhook_account_id)
            and config_data.get("auth_id") == webhook_account_id
        )

    async def verify_inbound_signature(
        self,
        url: str,
        webhook_data: dict[str, Any],
        headers: dict[str, str],
        body: str = "",
    ) -> bool:
        normalized_headers = {key.lower(): value for key, value in headers.items()}
        signature = normalized_headers.get("x-tryvox-signature", "")
        timestamp = normalized_headers.get("x-tryvox-timestamp", "")
        if not signature or not timestamp or not body:
            return False
        if self._signature_timestamp(signature) != timestamp:
            return False
        return self._verify_raw_signature(body, timestamp, signature)

    async def start_inbound_stream(
        self,
        *,
        websocket_url: str,
        workflow_run_id: int,
        normalized_data: NormalizedInboundData,
        backend_endpoint: str,
    ):
        from fastapi.responses import JSONResponse

        # `websocket_url` is the generic media-WS URL minted by
        # ws_auth.build_media_ws_url, ending in .../{workflow_id}/
        # {organization_id}/{workflow_run_id} and, when a capability-token
        # secret is configured, one more opaque token segment after that.
        # Locate the run ID (already known) rather than assuming a fixed
        # trailing segment count, so an extra token segment doesn't break
        # parsing; TryVox mints its own one-shot capability below, so any
        # generic token is simply dropped along with the rest of the URL.
        parsed = urlparse(websocket_url)
        segments = [part for part in parsed.path.split("/") if part]
        target = str(workflow_run_id)
        run_id_index = next(
            (i for i in range(len(segments) - 1, 1, -1) if segments[i] == target),
            None,
        )
        if run_id_index is None:
            raise ValueError("Invalid TryVox media WebSocket URL")
        try:
            workflow_id = int(segments[run_id_index - 2])
            organization_id = int(segments[run_id_index - 1])
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid TryVox media WebSocket URL") from exc

        websocket_url = (
            f"{parsed.scheme}://{parsed.netloc}/api/v1/telephony/tryvox/ws/"
            f"{workflow_id}/{organization_id}/{workflow_run_id}"
        )

        stream_token = await tryvox_security.issue_stream_token(
            workflow_id, organization_id, workflow_run_id
        )
        if stream_token is None:
            return JSONResponse(
                json.loads(
                    self._voxml_hangup_response(
                        "This call's media stream has already been connected."
                    )
                )
            )
        websocket_url = f"{websocket_url}?{urlencode({'token': stream_token})}"
        return JSONResponse(
            {
                "voxml_version": "1.0",
                "instructions": [
                    {
                        "verb": "Stream",
                        "url": websocket_url,
                        "track": "inbound_track",
                        "parameters": {
                            "provider": self.PROVIDER_NAME,
                            "workflow_run_id": str(workflow_run_id),
                        },
                    }
                ],
            }
        )

    async def configure_inbound(
        self, address: str, webhook_url: str | None
    ) -> ProviderSyncResult:
        if not self.application_id:
            return ProviderSyncResult(
                ok=False,
                message=(
                    "Set a TryVox Voice Application ID to configure inbound numbers"
                ),
            )
        if not self.auth_id or not self.auth_token:
            return ProviderSyncResult(
                ok=False, message="TryVox provider credentials are incomplete"
            )

        encoded_address = quote(address, safe="+")
        assignment_url = (
            f"{self.api_base_url}/v1/account/{self.auth_id}/numbers/"
            f"{encoded_address}/application"
        )
        try:
            async with aiohttp.ClientSession() as session:
                if not webhook_url:
                    async with session.delete(
                        assignment_url, auth=self._auth()
                    ) as response:
                        if response.status not in (200, 204, 404):
                            return ProviderSyncResult(
                                ok=False,
                                message=(
                                    f"TryVox number detach failed: "
                                    f"{response.status} {await response.text()}"
                                ),
                            )
                    return ProviderSyncResult(ok=True)

                application_url = (
                    f"{self.api_base_url}/v1/voice/applications/{self.application_id}"
                )
                async with session.patch(
                    application_url,
                    json={
                        "answer_url": webhook_url,
                        "answer_method": "POST",
                        "webhook_secret": self.webhook_secret,
                    },
                    auth=self._auth(),
                ) as response:
                    if response.status != 200:
                        return ProviderSyncResult(
                            ok=False,
                            message=(
                                f"TryVox application update failed: "
                                f"{response.status} {await response.text()}"
                            ),
                        )

                async with session.post(
                    assignment_url,
                    json={"application_id": self.application_id},
                    auth=self._auth(),
                ) as response:
                    if response.status not in (200, 201):
                        return ProviderSyncResult(
                            ok=False,
                            message=(
                                f"TryVox number assignment failed: "
                                f"{response.status} {await response.text()}"
                            ),
                        )
                return ProviderSyncResult(ok=True)
        except (aiohttp.ClientError, TimeoutError) as exc:
            logger.error(f"TryVox inbound configuration failed: {exc}")
            return ProviderSyncResult(
                ok=False, message=f"TryVox inbound configuration failed: {exc}"
            )

    @staticmethod
    def generate_error_response(error_type: str, message: str):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {
                "voxml_version": "1.0",
                "instructions": [
                    {
                        "verb": "Say",
                        "text": f"Sorry, the call could not be connected. {message}",
                    },
                    {"verb": "Hangup"},
                ],
            }
        )

    @staticmethod
    def generate_validation_error_response(error_type):
        from api.errors.telephony_errors import (
            TELEPHONY_ERROR_MESSAGES,
            TelephonyError,
        )

        message = TELEPHONY_ERROR_MESSAGES.get(
            error_type,
            TELEPHONY_ERROR_MESSAGES[TelephonyError.GENERAL_AUTH_FAILED],
        )
        return TryVoxProvider.generate_error_response(str(error_type), message)

    async def transfer_call(
        self,
        destination: str,
        transfer_id: str,
        conference_name: str,
        timeout: int = 30,
        **kwargs: Any,
    ) -> dict[str, Any]:
        raise NotImplementedError("TryVox transfer is not enabled in Dograh")

    def supports_transfers(self) -> bool:
        return False

    def _verify_raw_signature(
        self, body: str, timestamp: str, signature_header: str
    ) -> bool:
        if not self.webhook_secret or not timestamp:
            return False
        try:
            timestamp_value = int(timestamp)
        except ValueError:
            return False
        if abs(int(time.time()) - timestamp_value) > self.SIGNATURE_MAX_AGE_SECONDS:
            return False

        supplied = self._signature_digest(signature_header)
        if not supplied:
            return False
        expected = hmac.new(
            self.webhook_secret.encode("utf-8"),
            f"{timestamp}.{body}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, supplied)

    @staticmethod
    def _signature_timestamp(signature_header: str) -> str:
        for part in signature_header.split(","):
            key, separator, value = part.strip().partition("=")
            if separator and key == "t":
                return value
        return ""

    @staticmethod
    def _signature_digest(signature_header: str) -> str:
        for part in signature_header.split(","):
            key, separator, value = part.strip().partition("=")
            if separator and key == "v1":
                return value
        return ""

    @staticmethod
    async def _json_response(response: aiohttp.ClientResponse) -> dict[str, Any]:
        try:
            return await response.json()
        except (
            aiohttp.ContentTypeError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ):
            return {"message": await response.text()}
