"""AiSensy WhatsApp adapter.

AiSensy sends an approved Meta template by referencing an "API Campaign"
(`campaignName`) that binds the template — NOT the raw Meta template name. The
API key is passed in the JSON body (not a header). `templateParams` are POSITIONAL
({{1}},{{2}}...). Docs: https://wiki.aisensy.com (campaign API v2).
"""

from __future__ import annotations

from typing import List, Optional

import httpx
from loguru import logger

from api.services.whatsapp.base import (
    WhatsAppProvider,
    WhatsAppSendResult,
    normalize_destination,
)

AISENSY_ENDPOINT = "https://backend.aisensy.com/campaign/t1/api/v2"


class AiSensyProvider(WhatsAppProvider):
    name = "aisensy"

    def __init__(self, api_key: str, timeout: float = 15.0):
        self._api_key = api_key
        self._timeout = timeout

    async def send_template(
        self,
        *,
        to: str,
        campaign_name: str,
        template_params: List[str],
        sender_name: str,
        media_url: Optional[str] = None,
        media_filename: Optional[str] = None,
    ) -> WhatsAppSendResult:
        destination = normalize_destination(to)
        payload: dict = {
            "apiKey": self._api_key,
            "campaignName": campaign_name,
            "destination": destination,
            "userName": sender_name or "auto4you",
            "source": "voice-engine",
            "templateParams": [str(p) for p in (template_params or [])],
        }
        if media_url:
            payload["media"] = {"url": media_url}
            if media_filename:
                payload["media"]["filename"] = media_filename

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(AISENSY_ENDPOINT, json=payload)
        except Exception as exc:  # network / timeout
            logger.warning(f"AiSensy send failed (network) to {destination}: {exc}")
            return WhatsAppSendResult(ok=False, detail=f"network_error: {exc}")

        # AiSensy: HTTP 2xx + success != false == submitted. Body schema is loose.
        body: dict = {}
        try:
            body = resp.json() if resp.content else {}
        except Exception:
            body = {}
        ok = resp.is_success and body.get("success") is not False
        if ok:
            mid = body.get("submitted_message_id") or body.get("messageId")
            logger.info(
                f"AiSensy submitted template '{campaign_name}' to {destination}"
            )
            return WhatsAppSendResult(ok=True, detail="submitted", provider_message_id=mid)

        detail = body.get("errorMessage") or body.get("message") or f"http_{resp.status_code}"
        logger.warning(
            f"AiSensy send rejected to {destination}: {resp.status_code} {detail}"
        )
        return WhatsAppSendResult(ok=False, detail=str(detail))
