"""
WhatsApp implementation of the TelephonyProvider interface.

This module implements the core WhatsApp Business Calling API integration,
handling both user-initiated and business-initiated calls with proper
permission management and WebRTC media transport.

Key Features:
- User-Initiated Calls (UIC): Handle incoming WhatsApp voice calls
- Business-Initiated Calls (BIC): Initiate calls with permission management
- WebRTC Media: OPUS codec at 48kHz with DTLS/SRTP encryption
- Graph API Integration: REST API for call control and status queries
- Webhook Handling: Signature-validated webhook processing
- Permission Management: Temporary and permanent call permissions
"""

import hashlib
import hmac
import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import aiohttp
from fastapi import HTTPException
from loguru import logger

from api.enums import TelephonyCallStatus, WorkflowRunMode
from api.services.telephony import ws_auth
from api.services.telephony.base import (
    AnsweringMachineDetectionResult,
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


class WhatsAppProvider(TelephonyProvider):
    """
    WhatsApp implementation of TelephonyProvider.
    
    Supports both User-Initiated Calls (UIC) and Business-Initiated Calls (BIC)
    through the WhatsApp Business Calling API with WebRTC media transport.
    
    Attributes:
        PROVIDER_NAME: Provider identifier for registry
        WEBHOOK_ENDPOINT: Webhook endpoint path
        access_token: WhatsApp Business API access token
        phone_number_id: Business phone number ID
        webhook_verify_token: Token for webhook verification
        app_secret: App secret for signature validation
        from_numbers: List of available phone numbers
        business_initiated_calls_enabled: Whether BIC is enabled
    """

    PROVIDER_NAME = WorkflowRunMode.WHATSAPP.value
    WEBHOOK_ENDPOINT = "whatsapp"

    # WhatsApp Graph API base URL
    GRAPH_API_BASE_URL = "https://graph.facebook.com/v21.0"

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize WhatsAppProvider with configuration.

        Args:
            config: Dictionary containing:
                - access_token: WhatsApp Business API access token
                - phone_number_id: Business phone number ID
                - webhook_verify_token: Webhook verification token
                - app_secret: App secret for webhook signature validation
                - from_numbers: List of phone numbers to use
                - business_initiated_calls_enabled: Enable outbound calls
                - call_icon_visibility: Call icon display setting

        Raises:
            ValueError: If required configuration fields are missing
        """
        self.access_token = config.get("access_token")
        self.phone_number_id = config.get("phone_number_id")
        self.webhook_verify_token = config.get("webhook_verify_token")
        self.app_secret = config.get("app_secret")
        self.from_numbers = config.get("from_numbers", [])
        self.business_initiated_calls_enabled = config.get(
            "business_initiated_calls_enabled", False
        )
        self.call_icon_visibility = config.get("call_icon_visibility", "enabled")

        # Validate required fields
        if not self.access_token:
            raise ValueError("access_token is required for WhatsApp provider")
        if not self.phone_number_id:
            raise ValueError("phone_number_id is required for WhatsApp provider")
        if not self.webhook_verify_token:
            raise ValueError("webhook_verify_token is required for WhatsApp provider")
        if not self.app_secret:
            raise ValueError("app_secret is required for WhatsApp provider")

        # Handle both single number (string) and multiple numbers (list)
        if isinstance(self.from_numbers, str):
            self.from_numbers = [self.from_numbers]

        logger.info(
            f"WhatsAppProvider initialized for phone_number_id={self.phone_number_id}, "
            f"business_initiated_calls_enabled={self.business_initiated_calls_enabled}"
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
        Initiate an outbound call via WhatsApp Business API.

        For Business-Initiated Calls (BIC), this method requests user permission
        before proceeding. The call will only connect after the user grants
        permission through the WhatsApp interface.

        Args:
            to_number: The destination WhatsApp phone number (E.164 format)
            webhook_url: The URL to receive call events from Meta
            workflow_run_id: Optional workflow run ID for tracking
            from_number: Optional caller ID (not used in WhatsApp, but kept for interface compatibility)
            **kwargs: Additional provider-specific parameters

        Returns:
            CallInitiationResult with call details and status

        Raises:
            ValueError: If business-initiated calls are not enabled
            HTTPException: If Graph API call fails

        Note:
            WhatsApp requires user permission for business-initiated calls.
            The call status will be "permission_requested" until the user
            grants permission through the WhatsApp interface.
        """
        raise HTTPException(
            status_code=400,
            detail="Outbound calling via WhatsApp is currently under development. Only inbound calling is supported.",
        )

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        """
        Get the current status of a WhatsApp call.

        Args:
            call_id: The WhatsApp call ID from Graph API

        Returns:
            Dict containing call status information:
                - status: Mapped to TelephonyCallStatus enum
                - direction: Call direction (inbound/outbound)
                - duration: Call duration in seconds
                - from_number: Caller phone number
                - to_number: Called phone number

        Raises:
            HTTPException: If Graph API call fails

        Note:
            WhatsApp call statuses are mapped to Dograh's TelephonyCallStatus:
            - queued/ringing -> INITIATED/RINGING
            - in-progress -> IN_PROGRESS
            - completed -> COMPLETED
            - failed/busy/no-answer -> FAILED/BUSY/NO_ANSWER
        """
        logger.info(f"Querying WhatsApp call status for call_id={call_id}")

        endpoint = f"{self.GRAPH_API_BASE_URL}/{self.phone_number_id}/calls/{call_id}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(endpoint, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Graph API error: {error_text}")
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"Failed to get call status: {error_text}"
                    )

                data = await response.json()

        # Map WhatsApp status to Dograh TelephonyCallStatus
        whatsapp_status = data.get("status", "unknown")
        dograh_status = self._map_whatsapp_status_to_dograh(whatsapp_status)

        return {
            "status": dograh_status.value,
            "direction": data.get("direction", "unknown"),
            "duration": data.get("duration_seconds", 0),
            "from_number": data.get("from", ""),
            "to_number": data.get("to", ""),
            "raw_status": whatsapp_status,
        }

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        """WhatsApp does not expose call-cost data in this integration yet."""
        return {
            "cost_usd": 0.0,
            "duration": 0,
            "status": "unknown",
            "raw_response": {"call_id": call_id, "provider": self.PROVIDER_NAME},
            "error": "WhatsApp call-cost lookup is not implemented",
        }

    async def configure_inbound(
        self, address: str, webhook_url: Optional[str] = None
    ) -> ProviderSyncResult:
        """Configure inbound call handling for WhatsApp.

        WhatsApp requires manual webhook configuration in Meta Developer Console.
        """
        return ProviderSyncResult(
            ok=True,
            message="WhatsApp webhook configuration must be completed in Meta Developer Console.",
        )

    async def verify_inbound_signature(
        self,
        url: str,
        webhook_data: Dict[str, Any],
        headers: Dict[str, str],
        body: str = "",
    ) -> bool:
        """Verify inbound webhook signature from Meta.

        Meta uses HMAC-SHA256 with the app_secret to sign webhook payloads.
        The signature is provided in the X-Hub-Signature-256 header.

        Args:
            url: The webhook URL (not used in signature calculation)
            webhook_data: Parsed webhook payload
            headers: Request headers containing X-Hub-Signature-256
            body: Raw request body for signature verification

        Returns:
            True if signature is valid, False otherwise
        """
        if not self.app_secret:
            logger.warning("Missing app_secret for WhatsApp signature verification")
            return False

        signature_header = None
        for k, v in headers.items():
            if k.lower() == "x-hub-signature-256":
                signature_header = v
                break

        if not signature_header:
            logger.warning("Missing X-Hub-Signature-256 header")
            return False

        if not signature_header.startswith("sha256="):
            logger.warning(f"Invalid signature format: {signature_header}")
            return False

        expected_signature = signature_header[7:]

        calculated_signature = hmac.new(
            self.app_secret.encode("utf-8"),
            body.encode("utf-8") if isinstance(body, str) else body,
            hashlib.sha256,
        ).hexdigest()

        is_valid = hmac.compare_digest(calculated_signature, expected_signature)
        if not is_valid:
            logger.warning(
                f"WhatsApp signature verification failed. "
                f"Expected: {expected_signature}, Got: {calculated_signature}"
            )
        return is_valid

    def normalize_inbound_data(self, raw_webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        """
        Normalize WhatsApp webhook payload to standard format.

        Extracts call information from Meta's webhook structure and converts
        it to Dograh's NormalizedInboundData format for consistent processing.

        Args:
            raw_webhook_data: Raw webhook payload from Meta

        Returns:
            NormalizedInboundData with standardized call information

        Raises:
            ValueError: If webhook data structure is invalid

        Note:
            WhatsApp webhook structure:
            {
                "entry": [{
                    "changes": [{
                        "field": "calls",
                        "value": {
                            "display_phone_number": "+15551234567",
                            "call": {
                                "id": "call_id",
                                "direction": "inbound",
                                "from": "+15559876543",
                                "to": "+15551234567",
                                "status": "ringing"
                            }
                        }
                    }]
                }]
            }
        """
        if (
            not isinstance(raw_webhook_data, dict)
            or "entry" not in raw_webhook_data
            or not isinstance(raw_webhook_data.get("entry"), list)
            or not raw_webhook_data["entry"]
        ):
            raise ValueError("Invalid webhook data structure")

        try:
            entry = raw_webhook_data.get("entry", [{}])[0]
            changes = entry.get("changes", [{}])[0]
            call_value = changes.get("value", {})
            call_data = call_value.get("call", {})

            call_id = call_data.get("id")
            from_number = call_data.get("from")
            to_number = call_data.get("to")
            direction = call_data.get("direction")
            status = call_data.get("status")

            if not call_id:
                raise ValueError("Missing call_id in webhook data")

            return NormalizedInboundData(
                provider=self.PROVIDER_NAME,
                call_id=call_id,
                from_number=(
                    normalize_telephony_address(from_number).canonical
                    if from_number
                    else ""
                ),
                to_number=(
                    normalize_telephony_address(to_number).canonical
                    if to_number
                    else ""
                ),
                direction=direction or "unknown",
                call_status=status or "unknown",
                account_id=self.phone_number_id,
                raw_data=raw_webhook_data,
            )
        except (IndexError, KeyError, AttributeError) as e:
            logger.error(f"Failed to normalize WhatsApp webhook data: {e}")
            raise ValueError(f"Invalid webhook data structure: {e}")

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        """Detect WhatsApp webhook payloads."""
        if webhook_data.get("object") == "whatsapp_business_account":
            return True

        entry = webhook_data.get("entry")
        if isinstance(entry, list) and entry:
            for item in entry:
                if isinstance(item, dict):
                    changes = item.get("changes")
                    if isinstance(changes, list):
                        for change in changes:
                            if isinstance(change, dict):
                                if change.get("field") in ("calls", "messages"):
                                    return True
                                value = change.get("value")
                                if isinstance(value, dict) and (
                                    value.get("messaging_product") == "whatsapp"
                                    or "calls" in value
                                    or "call" in value
                                ):
                                    return True
        return False

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        """Parse WhatsApp webhook payload into the shared inbound shape."""
        entry = webhook_data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        calls = value.get("calls")
        if isinstance(calls, list) and calls:
            call = calls[0]
        else:
            call = value.get("call", {})
        metadata = value.get("metadata", {})

        call_id = call.get("id") or value.get("call_id") or ""
        from_number = call.get("from") or value.get("from") or ""
        to_number = call.get("to") or metadata.get("display_phone_number") or ""
        status = call.get("status") or value.get("status") or "unknown"
        direction = call.get("direction") or value.get("direction") or "inbound"
        account_id = metadata.get("phone_number_id") or metadata.get(
            "display_phone_number"
        )

        return NormalizedInboundData(
            provider=WhatsAppProvider.PROVIDER_NAME,
            call_id=call_id,
            from_number=(
                normalize_telephony_address(from_number).canonical
                if from_number
                else ""
            ),
            to_number=(
                normalize_telephony_address(to_number).canonical
                if to_number
                else ""
            ),
            direction=direction,
            call_status=status,
            account_id=account_id,
            raw_data=webhook_data,
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        """Match inbound WhatsApp webhooks by phone_number_id."""
        if not webhook_account_id:
            return False
        return config_data.get("phone_number_id") == webhook_account_id

    def validate_config(self) -> bool:
        """Return whether the WhatsApp credentials needed for WebRTC are present."""
        return bool(
            self.access_token
            and self.phone_number_id
            and self.webhook_verify_token
            and self.app_secret
        )

    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        """Verify a Meta webhook signature when the raw body is available."""
        raw_body = ""
        if isinstance(params, dict):
            raw_body = (
                params.get("_raw_body")
                or params.get("raw_body")
                or params.get("body")
                or ""
            )
        if not raw_body or not signature or not self.app_secret:
            return False

        if signature.startswith("sha256="):
            signature = signature[7:]

        calculated_signature = hmac.new(
            self.app_secret.encode("utf-8"),
            str(raw_body).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(calculated_signature, signature)

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize a WhatsApp webhook payload into shared callback fields."""
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        call = value.get("call", {})
        status = call.get("status") or value.get("status") or "unknown"

        return {
            "call_id": call.get("id") or value.get("call_id") or "",
            "status": self._map_whatsapp_status_to_dograh(status),
            "from_number": call.get("from") or "",
            "to_number": call.get("to") or "",
            "direction": call.get("direction") or value.get("direction"),
            "duration": call.get("duration") or value.get("duration_seconds"),
            "extra": data,
        }

    async def get_webhook_response(
        self, workflow_id: int, organization_id: int, workflow_run_id: int
    ) -> str:
        """WhatsApp call-control is not XML-based; return an empty body."""
        logger.warning(
            "get_webhook_response called for WhatsApp - returning empty response. "
            "WhatsApp inbound handling is implemented in the dedicated webhook routes."
        )
        return ""

    async def handle_websocket(
        self,
        websocket: "WebSocket",
        workflow_id: int,
        organization_id: int,
        workflow_run_id: int,
    ) -> None:
        """Run the shared telephony pipeline for a WhatsApp media websocket."""
        from api.db import db_client
        from api.services.pipecat.run_pipeline import run_pipeline_telephony

        workflow_run = await db_client.get_workflow_run(
            workflow_run_id, organization_id=organization_id
        )
        call_id = self.phone_number_id
        if workflow_run and workflow_run.gathered_context:
            call_id = (
                workflow_run.gathered_context.get("call_id")
                or workflow_run.gathered_context.get("call_uuid")
                or call_id
            )

        logger.info(
            f"[WhatsApp] Starting pipeline for workflow_run {workflow_run_id}, "
            f"call_id={call_id}"
        )
        await run_pipeline_telephony(
            websocket,
            provider_name=self.PROVIDER_NAME,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            organization_id=organization_id,
            call_id=call_id,
            transport_kwargs={"call_id": call_id},
        )

    async def start_inbound_stream(
        self,
        *,
        websocket_url: str,
        workflow_run_id: int,
        normalized_data,
        backend_endpoint: str,
    ):
        """Return a minimal response because WhatsApp handles media via websocket."""
        from fastapi import Response

        logger.info(
            f"WhatsApp start_inbound_stream called for call_id={normalized_data.call_id}"
        )
        return Response(content="", status_code=204)

    @staticmethod
    def generate_error_response(error_type: str, message: str) -> tuple:
        """Generate a simple JSON error response."""
        from fastapi import Response

        return Response(
            content=json.dumps({"error": error_type, "message": message}),
            media_type="application/json",
        )

    @staticmethod
    def generate_validation_error_response(error_type) -> Any:
        """Generate WhatsApp-specific error response for validation failures."""
        from fastapi import Response

        from api.errors.telephony_errors import TELEPHONY_ERROR_MESSAGES, TelephonyError

        message = TELEPHONY_ERROR_MESSAGES.get(
            error_type, TELEPHONY_ERROR_MESSAGES[TelephonyError.GENERAL_AUTH_FAILED]
        )
        return Response(
            content=json.dumps({"error": error_type.value if hasattr(error_type, "value") else str(error_type), "message": message}),
            media_type="application/json",
        )

    async def validate_phone_number(self, address: str) -> ProviderSyncResult:
        """Check that Meta exposes this WhatsApp phone number on the connected WABA."""
        try:
            available_numbers = await self.get_available_phone_numbers()
        except HTTPException as exc:
            return ProviderSyncResult(ok=False, message=str(exc.detail))

        normalized = normalize_telephony_address(address).canonical
        if normalized in available_numbers:
            return ProviderSyncResult(ok=True)

        return ProviderSyncResult(
            ok=False,
            message=(
                f"Phone number {normalized} is not owned by this WhatsApp "
                "Business Account."
            ),
        )

    async def transfer_call(
        self,
        destination: str,
        transfer_id: str,
        conference_name: str,
        timeout: int = 30,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """WhatsApp Calling does not currently support transfers in Dograh."""
        return {
            "call_sid": transfer_id,
            "status": "failed",
            "provider": self.PROVIDER_NAME,
            "message": "WhatsApp call transfer is not implemented",
        }

    def supports_transfers(self) -> bool:
        """WhatsApp transfer support is not implemented yet."""
        return False

    async def get_available_phone_number_records(self) -> List[Dict[str, Any]]:
        """Return WhatsApp Business number records with their Meta phone_number_id."""
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        records: List[Dict[str, Any]] = []
        seen_addresses: set[str] = set()

        # 1. Query the configured phone_number_id directly for display_phone_number
        phone_number_endpoint = f"{self.GRAPH_API_BASE_URL}/{self.phone_number_id}"
        waba_id: Optional[str] = None
        self.is_full_inventory = False
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    phone_number_endpoint,
                    headers=headers,
                    params={
                        "fields": "display_phone_number,verified_name,id,whatsapp_business_account"
                    },
                ) as response:
                    if response.status == 200:
                        payload = await response.json()
                        direct_number = payload.get("display_phone_number")
                        direct_id = payload.get("id") or self.phone_number_id
                        waba_info = payload.get("whatsapp_business_account")
                        if isinstance(waba_info, dict) and waba_info.get("id"):
                            waba_id = str(waba_info["id"])
                        if direct_number:
                            try:
                                canonical = normalize_telephony_address(
                                    direct_number
                                ).canonical
                                if canonical not in seen_addresses:
                                    seen_addresses.add(canonical)
                                    records.append({
                                        "address": canonical,
                                        "extra_metadata": {
                                            "meta_phone_number_id": str(direct_id),
                                            "phone_number_id": str(direct_id),
                                        },
                                    })
                            except ValueError:
                                logger.warning(
                                    "Skipping unparseable direct WhatsApp phone number "
                                    f"{direct_number!r}"
                                )
                    else:
                        error_text = await response.text()
                        logger.warning(
                            f"Direct query on phone_number_id={self.phone_number_id} "
                            f"returned {response.status}: {error_text}"
                        )
            except Exception as e:
                logger.warning(f"Error querying direct phone number endpoint: {e}")

            # 2. Query the /phone_numbers edge on the WABA:
            # - If step 1 gave us waba_id, query {waba_id}/phone_numbers to discover
            #   all other numbers belonging to this WhatsApp Business Account.
            # - If direct lookup did not resolve a number (e.g. self.phone_number_id is
            #   itself a WABA ID), query {self.phone_number_id}/phone_numbers.
            target_waba = waba_id or (self.phone_number_id if not records else None)
            if target_waba:
                next_url: Optional[str] = f"{self.GRAPH_API_BASE_URL}/{target_waba}/phone_numbers"
                all_pages_succeeded = True
                had_successful_page = False
                while next_url:
                    try:
                        async with session.get(next_url, headers=headers) as response:
                            if response.status == 200:
                                had_successful_page = True
                                payload = await response.json()
                                for record in payload.get("data") or []:
                                    display_phone_number = record.get("display_phone_number")
                                    phone_id = record.get("id")
                                    if not display_phone_number:
                                        continue
                                    try:
                                        canonical = normalize_telephony_address(
                                            display_phone_number
                                        ).canonical
                                        if canonical not in seen_addresses:
                                            seen_addresses.add(canonical)
                                            records.append({
                                                "address": canonical,
                                                "extra_metadata": {
                                                    "meta_phone_number_id": str(phone_id or target_waba),
                                                    "phone_number_id": str(phone_id or target_waba),
                                                },
                                            })
                                    except ValueError:
                                        logger.warning(
                                            "Skipping unparseable WhatsApp phone number "
                                            f"{display_phone_number!r}"
                                        )
                                paging = payload.get("paging") or {}
                                next_url = paging.get("next")
                            else:
                                error_text = await response.text()
                                logger.warning(
                                    f"Query on /phone_numbers edge for ID {target_waba} "
                                    f"returned {response.status}: {error_text}"
                                )
                                all_pages_succeeded = False
                                break
                    except Exception as e:
                        logger.warning(f"Error querying /phone_numbers edge: {e}")
                        all_pages_succeeded = False
                        break

                if all_pages_succeeded and had_successful_page:
                    self.is_full_inventory = True

        # 3. Fallback to statically configured from_numbers if any
        if not records and self.from_numbers:
            for num in self.from_numbers:
                try:
                    canonical = normalize_telephony_address(num).canonical
                    if canonical not in seen_addresses:
                        seen_addresses.add(canonical)
                        records.append({
                            "address": canonical,
                            "extra_metadata": {
                                "meta_phone_number_id": str(self.phone_number_id),
                                "phone_number_id": str(self.phone_number_id),
                            },
                        })
                except ValueError:
                    pass

        if not records:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Failed to resolve WhatsApp phone number for ID {self.phone_number_id}. "
                    "Please check your Phone Number ID and Access Token permissions."
                ),
            )

        return records

    async def get_available_phone_numbers(self) -> List[str]:
        """Return the WhatsApp Business numbers exposed by Meta for this configuration."""
        records = await self.get_available_phone_number_records()
        return [r["address"] for r in records]

    def _map_whatsapp_status_to_dograh(self, whatsapp_status: str) -> TelephonyCallStatus:
        """Map WhatsApp call status to Dograh TelephonyCallStatus enum.
        
        Args:
            whatsapp_status: Status string from WhatsApp Graph API
            
        Returns:
            Corresponding TelephonyCallStatus enum value
        """
        status_mapping = {
            "queued": TelephonyCallStatus.INITIATED,
            "ringing": TelephonyCallStatus.RINGING,
            "in-progress": TelephonyCallStatus.IN_PROGRESS,
            "answered": TelephonyCallStatus.ANSWERED,
            "completed": TelephonyCallStatus.COMPLETED,
            "failed": TelephonyCallStatus.FAILED,
            "busy": TelephonyCallStatus.BUSY,
            "no-answer": TelephonyCallStatus.NO_ANSWER,
            "canceled": TelephonyCallStatus.CANCELED,
            "permission_requested": TelephonyCallStatus.INITIATED,
            "permission_denied": TelephonyCallStatus.FAILED,
        }

        return status_mapping.get(whatsapp_status.lower(), TelephonyCallStatus.ERROR)

    async def _request_call_permission(
        self,
        to_number: str,
        workflow_run_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Request call permission from WhatsApp user for business-initiated call.
        
        Args:
            to_number: Destination phone number
            workflow_run_id: Optional workflow run ID for tracking
            
        Returns:
            Dict with permission request result from Graph API
        """
        endpoint = f"{self.GRAPH_API_BASE_URL}/{self.phone_number_id}/calls"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

        clean_to = to_number.lstrip("+")
        payload: Dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": clean_to,
        }
        if workflow_run_id:
            payload["biz_opaque_callback_data"] = str(workflow_run_id)

        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, headers=headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Permission request failed: {error_text}")
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"Permission request failed: {error_text}"
                    )

                return await response.json()
