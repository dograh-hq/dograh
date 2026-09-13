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
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import aiohttp
from fastapi import HTTPException
from loguru import logger

from api.enums import TelephonyCallStatus, WorkflowRunMode
from api.services.telephony.providers.whatsapp.config import (
    DEFAULT_WHATSAPP_PERMISSION_MESSAGE,
    GRANTED_PERMISSION_STATUSES,
    normalize_whatsapp_permission_status,
    parse_whatsapp_expiration,
)


def _local_permission_is_usable(perm: Any, now: datetime) -> bool:
    """Decide whether a stored permission may stand in for a live Meta answer.

    Only used when Meta's permission API is unusable. A permanent grant never
    expires, so it is safe to trust offline. A temporary grant is only trusted
    while we hold a concrete expiry that is still in the future: a NULL
    ``expires_at`` means we never learned when Meta's grant lapses, not that it
    lasts forever.
    """
    if not perm:
        return False
    if perm.status == "granted_permanent":
        return True
    if perm.status == "granted_temporary":
        return bool(perm.expires_at) and now <= perm.expires_at
    return False


from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    ProviderSyncResult,
    TelephonyPermissionRequiredError,
    TelephonyProvider,
)
from api.utils.telephony_address import normalize_telephony_address

if TYPE_CHECKING:
    from fastapi import WebSocket


class WhatsAppPermissionRequiredError(HTTPException, TelephonyPermissionRequiredError):
    """Raised when attempting to place an outbound WhatsApp call without recipient permission."""

    def __init__(
        self,
        phone_number: str,
        status: Optional[str] = None,
        can_request_permission: bool = True,
        request_limit_reason: Optional[str] = None,
    ):
        self.phone_number = phone_number
        msg = (
            f"Permission to call {phone_number} has not been granted or has been revoked by the recipient. "
            "Please send a WhatsApp call permission request first."
        )
        HTTPException.__init__(
            self,
            status_code=400,
            detail=msg,
        )
        TelephonyPermissionRequiredError.__init__(
            self,
            message=msg,
            status=status or "missing",
            can_request_permission=can_request_permission,
            request_limit_reason=request_limit_reason,
        )


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
        self.default_permission_message = config.get("default_permission_message")
        self.telephony_configuration_id = config.get("telephony_configuration_id")
        self.organization_id = config.get("organization_id")

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
        Initiate an outbound call via WhatsApp Business Calling API.

        For Business-Initiated Calls (BIC), this method verifies country eligibility
        and checks recipient permission before placing the WebRTC call.

        Args:
            to_number: The destination WhatsApp phone number (E.164 format)
            webhook_url: The URL to receive call events from Meta
            workflow_run_id: Optional workflow run ID for tracking
            from_number: Optional caller ID / business phone number
            **kwargs: Additional parameters (organization_id, workflow_id, telephony_configuration_id, etc.)

        Returns:
            CallInitiationResult with call details and status

        Raises:
            HTTPException: 400 if BIC is disabled, country is restricted, or permission missing.
        """
        if not self.business_initiated_calls_enabled:
            raise HTTPException(
                status_code=400,
                detail="Business-initiated calling is disabled for this WhatsApp configuration. Enable it in Telephony Configurations.",
            )

        from datetime import datetime, timezone

        try:
            from datetime import UTC
        except ImportError:
            UTC = timezone.utc

        from api.db import db_client
        from api.services.telephony.providers.whatsapp.restrictions import (
            validate_destination_country,
        )
        from api.services.telephony.providers.whatsapp.service import (
            WHATSAPP_CALL_KEY_PREFIX,
            register_outbound_active_connection,
            unregister_outbound_active_connection,
        )
        from api.services.telephony.providers.whatsapp.service import (
            get_or_create_whatsapp_client as _get_or_create_whatsapp_client,
        )
        from api.services.telephony.providers.whatsapp.service import (
            get_whatsapp_redis as _get_redis,
        )
        from api.utils.telephony_address import (
            canonicalize_e164,
            normalize_telephony_address,
        )

        # 1. Canonicalise, then pre-validate country restrictions.
        #
        # Campaign ingest stores leads in strict E.164, but rows ingested
        # before it did - and any caller passing a number the way a person
        # writes it - still arrive formatted ("+44 7123 456789"). Only
        # presentation characters are removed here, so this never invents a
        # country code: what cannot be canonicalised is passed through
        # untouched and rejected by the strict check below, exactly as before.
        to_number = canonicalize_e164(to_number) or to_number
        validate_destination_country(to_number)

        raw_to = to_number.strip()
        digits_to = re.sub(r"\D", "", raw_to)
        candidates = [raw_to, raw_to.lstrip("+")]
        if digits_to:
            candidates.extend([digits_to, f"+{digits_to}"])
        try:
            canonical_to = normalize_telephony_address(raw_to).canonical
            candidates.extend([canonical_to, canonical_to.lstrip("+")])
        except Exception:
            pass
        lookup_candidates = list(dict.fromkeys([c for c in candidates if c]))
        clean_to = digits_to if digits_to else raw_to.lstrip("+")

        org_id = kwargs.get("organization_id")
        workflow_id = kwargs.get("workflow_id")
        telephony_config_id = kwargs.get("telephony_configuration_id")
        user_id = kwargs.get("user_id", 0)

        # 2. Permission check: DB lookup and fallback to Meta API
        now = datetime.now(UTC)
        has_permission = False
        perm = None

        if telephony_config_id:
            for cand in lookup_candidates:
                perm = await db_client.get_whatsapp_call_permission(
                    telephony_configuration_id=telephony_config_id,
                    recipient_phone_number=cand,
                )
                if perm:
                    break
        else:
            for cand in lookup_candidates:
                perm = await db_client.get_whatsapp_call_permission_by_phone_id(
                    phone_number_id=self.phone_number_id,
                    recipient_phone_number=cand,
                )
                if perm:
                    break

        client = _get_or_create_whatsapp_client(
            phone_number_id=self.phone_number_id,
            access_token=self.access_token,
            app_secret=self.app_secret,
        )

        has_permission = False
        meta_checked = False
        meta_status = None
        can_request_perm = True
        try:
            meta_res = await client.check_call_permission(user_wa_id=clean_to)
            if "error" in meta_res:
                err_data = meta_res.get("error") or {}
                err_msg = err_data.get("message") or "Unknown Meta API error"
                err_code = err_data.get("code")
                logger.warning(
                    f"[WhatsApp] Live call permission check returned Meta API error: code={err_code}, {err_msg}"
                )
                is_token_err = (
                    err_code in (190, 102)
                    or str(err_code) in ("190", "102")
                    or err_data.get("type") == "OAuthException"
                    or "token" in err_msg.lower()
                    or "session" in err_msg.lower()
                )
                if is_token_err:
                    raise HTTPException(
                        status_code=401,
                        detail=(
                            f"Meta API Error ({err_code}): {err_msg}. "
                            "The WhatsApp access token has expired or is invalid. "
                            "Please generate a fresh token in Meta Business Manager and update your Telephony Configuration."
                        ),
                    )
                has_permission = _local_permission_is_usable(perm, now)
            else:
                meta_checked = True
                meta_perm = meta_res.get("permission") or {}
                meta_status = meta_perm.get("status") or meta_res.get("status")
                can_start_call = False
                for act in meta_res.get("actions") or []:
                    if act.get("action_name") == "start_call" and act.get(
                        "can_perform_action", False
                    ):
                        can_start_call = True
                    if act.get("action_name") == "send_call_permission_request":
                        can_request_perm = bool(act.get("can_perform_action", True))

                # One classification for both the decision and what gets
                # stored. The raw status was being compared twice - once
                # normalised, once not - so a spelling Meta varies in case or
                # whitespace ("Permanent") allowed the call and then stored it
                # as granted_temporary. That record carries no expiry, and the
                # offline fallback refuses a temporary grant without one, so a
                # permanent grant turned into "no permission" the moment Meta
                # was unreachable.
                normalized_meta_status = normalize_whatsapp_permission_status(
                    meta_status
                )
                if (
                    can_start_call
                    or normalized_meta_status in GRANTED_PERMISSION_STATUSES
                ):
                    has_permission = True
                    try:
                        if org_id and telephony_config_id:
                            expiration = meta_perm.get(
                                "expiration_time"
                            ) or meta_res.get("expiration")
                            meta_expires_at = parse_whatsapp_expiration(expiration)
                            perm_status = (
                                "granted_permanent"
                                if normalized_meta_status == "granted_permanent"
                                else "granted_temporary"
                            )
                            await db_client.upsert_whatsapp_call_permission(
                                organization_id=org_id,
                                telephony_configuration_id=telephony_config_id,
                                phone_number_id=self.phone_number_id,
                                recipient_phone_number=to_number,
                                status=perm_status,
                                permission_type="permanent"
                                if perm_status == "granted_permanent"
                                else "temporary",
                                expires_at=meta_expires_at,
                                granted_at=now,
                            )
                    except Exception as db_err:
                        logger.warning(
                            f"[WhatsApp] Failed saving granted permission to local DB: {db_err}"
                        )
                else:
                    # Meta answered and the recipient cannot be called. Store the
                    # state through the shared alias map rather than a local
                    # transcription of it: "no_permission" (never granted) has to
                    # stay distinct from "denied" (explicitly refused), because
                    # the parked-run sweep fails campaign runs outright on
                    # "denied". An unrecognised or future status normalizes to
                    # None and is skipped - overwriting a good record with a
                    # value nothing downstream understands is worse than leaving
                    # the record as it was.
                    normalized_status = normalize_whatsapp_permission_status(
                        meta_status
                    )
                    if meta_status and normalized_status is None:
                        logger.warning(
                            f"[WhatsApp] Unrecognised permission status '{meta_status}' reported by Meta "
                            f"for {to_number}; leaving the stored permission record untouched."
                        )
                    if org_id and telephony_config_id and normalized_status:
                        await db_client.upsert_whatsapp_call_permission(
                            organization_id=org_id,
                            telephony_configuration_id=telephony_config_id,
                            phone_number_id=self.phone_number_id,
                            recipient_phone_number=to_number,
                            status=normalized_status,
                            expires_at=None,
                        )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(
                f"[WhatsApp] Live call permission check failed against Meta API: {e}"
            )
            # Fallback to local DB record if Meta API is temporarily unreachable
            has_permission = _local_permission_is_usable(perm, now)

        if not has_permission:
            raise WhatsAppPermissionRequiredError(
                phone_number=to_number,
                status=meta_status or (perm.status if perm else None),
                can_request_permission=can_request_perm,
            )

        # 3. Pre-validate registration metadata before initiating call
        if not workflow_run_id or not org_id or not workflow_id:
            logger.error(
                f"[WhatsApp] Cannot initiate outbound call to {to_number}: missing registration metadata "
                f"(workflow_run_id={workflow_run_id}, organization_id={org_id}, workflow_id={workflow_id})"
            )
            raise HTTPException(
                status_code=400,
                detail="workflow_run_id, organization_id, and workflow_id are required to initiate an outbound call",
            )

        # Initiate WebRTC outbound call via Meta Graph API
        logger.info(
            f"[WhatsApp] Placing outbound call from {self.phone_number_id} to {to_number}, "
            f"workflow_run_id={workflow_run_id}"
        )
        try:
            call_id, connection, resp = await client.initiate_outbound_call(
                to=clean_to,
                biz_opaque_callback_data=str(workflow_run_id),
            )
        except Exception as e:
            logger.error(f"[WhatsApp] Failed to initiate outbound call: {e}")
            raise HTTPException(
                status_code=400,
                detail=f"Failed to initiate WhatsApp call: {str(e)}",
            )

        # 4. Register active WebRTC connection on this worker.
        #
        # Redis state is written first because it is the step that actually
        # talks to the network and can fail. Registration after it is
        # synchronous, so the common failure leaves nothing half-registered,
        # and the pipeline only starts once the cross-worker lookup key exists.
        redis_key = f"{WHATSAPP_CALL_KEY_PREFIX}{call_id}"
        redis = None
        redis_key_written = False
        try:
            redis = await _get_redis()
            if redis:
                await redis.setex(
                    redis_key,
                    3600,
                    json.dumps(
                        {
                            "workflow_run_id": workflow_run_id,
                            "organization_id": org_id,
                            "phone_number_id": self.phone_number_id,
                        }
                    ),
                )
                redis_key_written = True

            register_outbound_active_connection(
                call_id=call_id,
                connection=connection,
                workflow_run_id=workflow_run_id,
                organization_id=org_id,
                phone_number_id=self.phone_number_id,
                workflow_id=workflow_id,
                user_id=user_id,
            )
        except Exception as reg_err:
            logger.error(
                f"[WhatsApp] Failed to register outbound connection for call {call_id}: {reg_err}"
            )
            try:
                # The service layer owns this state, so it owns the teardown:
                # registry entries, the answer gate, and the pipeline task all
                # come down together, keyed by call_id.
                await unregister_outbound_active_connection(call_id)
            except Exception as unregister_err:
                logger.warning(
                    f"[WhatsApp] Error unregistering rolled-back call {call_id}: {unregister_err}"
                )
            try:
                if hasattr(connection, "disconnect"):
                    await connection.disconnect()
                elif hasattr(connection, "close"):
                    await connection.close()
            except Exception:
                pass
            try:
                await client.terminate_call(call_id)
            except Exception:
                pass
            if redis_key_written and redis:
                try:
                    await redis.delete(redis_key)
                except Exception:
                    pass
            raise HTTPException(
                status_code=500,
                detail=f"Failed to register active call connection: {reg_err}",
            )

        # 5. Resolve caller ID
        caller_id = from_number or (
            self.from_numbers[0] if self.from_numbers else self.phone_number_id
        )

        return CallInitiationResult(
            call_id=call_id,
            status="initiated",
            caller_number=caller_id,
            provider_metadata={
                "call_id": call_id,
                "phone_number_id": self.phone_number_id,
                "destination": to_number,
            },
            raw_response=resp,
        )

    async def end_call(
        self, call_id: str, workflow_run_id: int, organization_id: int
    ) -> bool:
        """Terminate a live WhatsApp call at Meta and tear down local state."""
        from api.services.telephony.providers.whatsapp.service import (
            terminate_whatsapp_call_by_id,
        )

        return await terminate_whatsapp_call_by_id(
            call_id, workflow_run_id, organization_id
        )

    async def send_call_permission_request(
        self,
        to_number: str,
        body_text: Optional[str] = None,
        organization_id: Optional[int] = None,
        telephony_configuration_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Send a WhatsApp call permission request message to the recipient and persist pending record."""
        from api.services.telephony.providers.whatsapp.service import (
            get_or_create_whatsapp_client as _get_or_create_whatsapp_client,
        )

        client = _get_or_create_whatsapp_client(
            phone_number_id=self.phone_number_id,
            access_token=self.access_token,
            app_secret=self.app_secret,
        )
        clean_to = to_number.strip().lstrip("+")
        text = (
            (body_text or "").strip()
            or (self.default_permission_message or "").strip()
            or DEFAULT_WHATSAPP_PERMISSION_MESSAGE
        )
        res = await client.send_call_permission_request(
            to=clean_to,
            body_text=text,
        )
        if isinstance(res, dict) and "error" in res:
            err = res.get("error", {})
            code = err.get("code")
            msg = err.get("message")
            is_token_err = (
                code in (190, 102)
                or str(code) in ("190", "102")
                or err.get("type") == "OAuthException"
                or "token" in str(msg).lower()
                or "session" in str(msg).lower()
            )
            if is_token_err:
                raise HTTPException(
                    status_code=401,
                    detail=(
                        f"Meta API Error ({code}): {msg}. "
                        "The WhatsApp access token has expired or is invalid. "
                        "Please generate a fresh token in Meta Business Manager and update your Telephony Configuration."
                    ),
                )
            raise RuntimeError(
                f"Failed to send permission request: {msg} (code {code})"
            )

        # Persist pending permission record so webhooks can correlate replies via meta_message_id
        messages = res.get("messages") or [] if isinstance(res, dict) else []
        message_id = messages[0].get("id") if messages else None
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=7)

        try:
            from api.db import db_client

            cfg_id = telephony_configuration_id or self.telephony_configuration_id
            org_id = organization_id or self.organization_id

            if not (cfg_id and org_id):
                cfg = await db_client.get_whatsapp_configuration_by_phone_number_id(
                    self.phone_number_id
                )
                if cfg:
                    cfg_id = cfg.id
                    org_id = cfg.organization_id

            if cfg_id and org_id:
                await db_client.upsert_whatsapp_call_permission(
                    organization_id=org_id,
                    telephony_configuration_id=cfg_id,
                    phone_number_id=self.phone_number_id,
                    recipient_phone_number=to_number,
                    status="pending",
                    meta_message_id=message_id,
                    expires_at=expires_at,
                )
                logger.info(
                    f"[WhatsApp] Persisted pending call permission for {to_number} "
                    f"(message_id={message_id}, config_id={cfg_id})"
                )
        except Exception as persist_err:
            logger.warning(
                f"[WhatsApp] Failed to persist pending call permission for {to_number}: {persist_err}"
            )

        return res

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
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(endpoint, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Graph API error: {error_text}")
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"Failed to get call status: {error_text}",
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

    def normalize_inbound_data(
        self, raw_webhook_data: Dict[str, Any]
    ) -> NormalizedInboundData:
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
                normalize_telephony_address(to_number).canonical if to_number else ""
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
            content=json.dumps(
                {
                    "error": error_type.value
                    if hasattr(error_type, "value")
                    else str(error_type),
                    "message": message,
                }
            ),
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
                                    records.append(
                                        {
                                            "address": canonical,
                                            "extra_metadata": {
                                                "meta_phone_number_id": str(direct_id),
                                                "phone_number_id": str(direct_id),
                                            },
                                        }
                                    )
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
                next_url: Optional[str] = (
                    f"{self.GRAPH_API_BASE_URL}/{target_waba}/phone_numbers"
                )
                all_pages_succeeded = True
                had_successful_page = False
                while next_url:
                    try:
                        async with session.get(next_url, headers=headers) as response:
                            if response.status == 200:
                                had_successful_page = True
                                payload = await response.json()
                                for record in payload.get("data") or []:
                                    display_phone_number = record.get(
                                        "display_phone_number"
                                    )
                                    phone_id = record.get("id")
                                    if not display_phone_number:
                                        continue
                                    try:
                                        canonical = normalize_telephony_address(
                                            display_phone_number
                                        ).canonical
                                        if canonical not in seen_addresses:
                                            seen_addresses.add(canonical)
                                            records.append(
                                                {
                                                    "address": canonical,
                                                    "extra_metadata": {
                                                        "meta_phone_number_id": str(
                                                            phone_id or target_waba
                                                        ),
                                                        "phone_number_id": str(
                                                            phone_id or target_waba
                                                        ),
                                                    },
                                                }
                                            )
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
                        records.append(
                            {
                                "address": canonical,
                                "extra_metadata": {
                                    "meta_phone_number_id": str(self.phone_number_id),
                                    "phone_number_id": str(self.phone_number_id),
                                },
                            }
                        )
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

    def _map_whatsapp_status_to_dograh(
        self, whatsapp_status: str
    ) -> TelephonyCallStatus:
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
        self, to_number: str, workflow_run_id: Optional[int] = None
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
            "Content-Type": "application/json",
        }

        clean_to = to_number.lstrip("+")
        payload: Dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": clean_to,
        }
        if workflow_run_id:
            payload["biz_opaque_callback_data"] = str(workflow_run_id)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                endpoint, headers=headers, json=payload
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Permission request failed: {error_text}")
                    raise HTTPException(
                        status_code=response.status,
                        detail=f"Permission request failed: {error_text}",
                    )

                return await response.json()
