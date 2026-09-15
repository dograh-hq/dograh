import base64
import hashlib
import hmac
import time
from typing import Any, Dict, Optional

import aiohttp
from loguru import logger

from api.constants import (
    RAZORPAY_KEY_ID,
    RAZORPAY_KEY_SECRET,
    RAZORPAY_WEBHOOK_SECRET,
)

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"


class RazorpayService:
    """Service to interact with the Razorpay REST API and verify signatures."""

    def __init__(
        self,
        key_id: str = RAZORPAY_KEY_ID,
        key_secret: str = RAZORPAY_KEY_SECRET,
        webhook_secret: str = RAZORPAY_WEBHOOK_SECRET,
    ):
        self.key_id = key_id
        self.key_secret = key_secret
        self.webhook_secret = webhook_secret

    def _get_auth_header(self) -> Dict[str, str]:
        auth_bytes = f"{self.key_id}:{self.key_secret}".encode("utf-8")
        encoded_auth = base64.b64encode(auth_bytes).decode("utf-8")
        return {
            "Authorization": f"Basic {encoded_auth}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def generate_receipt_id(organization_id: int) -> str:
        """Generate a receipt identifier for Razorpay orders.

        Max length allowed by Razorpay is 40 characters.
        Format: rcpt_org{org_id}_{unix_timestamp}
        Example: rcpt_org1_1726404123
        This allows direct filtering in the Razorpay dashboard by typing 'rcpt_org1'.
        """
        timestamp = int(time.time())
        return f"rcpt_org{organization_id}_{timestamp}"

    async def create_order(
        self,
        amount_paise: int,
        currency: str,
        receipt: str,
        notes: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a Razorpay order.

        Args:
            amount_paise: Amount in smallest currency unit (e.g. paise for INR).
            currency: Currency code (e.g. 'INR').
            receipt: Receipt identifier string (max 40 chars).
            notes: Key-value metadata pairs.

        Returns:
            Dict containing order details including 'id', 'amount', 'currency', etc.
        """
        payload = {
            "amount": amount_paise,
            "currency": currency,
            "receipt": receipt,
            "payment_capture": 1,  # Auto-capture payment upon authorization
            "notes": notes or {},
        }

        url = f"{RAZORPAY_API_BASE}/orders"
        headers = self._get_auth_header()

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                resp_data = await response.json()
                if response.status not in (200, 201):
                    logger.error(
                        f"[Razorpay] Order creation failed HTTP {response.status}: {resp_data}"
                    )
                    error_msg = resp_data.get("error", {}).get(
                        "description", "Failed to create Razorpay order"
                    )
                    raise RuntimeError(f"Razorpay error: {error_msg}")
                logger.info(
                    f"[Razorpay] Created order {resp_data.get('id')} with receipt {receipt} for {amount_paise} paise"
                )
                return resp_data

    def verify_payment_signature(
        self,
        order_id: str,
        payment_id: str,
        signature: str,
    ) -> bool:
        """Verify Razorpay checkout payment signature using HMAC SHA256.

        Signature string format: order_id + "|" + payment_id
        """
        if not signature or not order_id or not payment_id:
            return False

        message = f"{order_id}|{payment_id}".encode("utf-8")
        secret_bytes = self.key_secret.encode("utf-8")
        generated_signature = hmac.new(
            secret_bytes, message, hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(generated_signature, signature)

    def verify_webhook_signature(
        self,
        raw_body: bytes,
        signature: str,
    ) -> bool:
        """Verify Razorpay webhook payload signature using HMAC SHA256."""
        if not signature or not raw_body:
            return False

        secret_bytes = self.webhook_secret.encode("utf-8")
        generated_signature = hmac.new(
            secret_bytes, raw_body, hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(generated_signature, signature)


razorpay_service = RazorpayService()
