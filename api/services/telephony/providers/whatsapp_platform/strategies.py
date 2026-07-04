"""WhatsApp Platform call operation strategies.

Two strategies:

1. WhatsAppTransferStrategy
   Called by the Pipecat pipeline when the AI decides to transfer to a human.
   POSTs to /dograh-webhook/transfer → Platform re-broadcasts to human agents.

2. WhatsAppHangupStrategy
   Called by the Pipecat pipeline when the AI ends the call.
   POSTs to /dograh-webhook/hangup → Platform calls Meta terminate API.
"""

from typing import Any, Dict

import aiohttp
from loguru import logger
from pipecat.serializers.call_strategies import HangupStrategy, TransferStrategy


class WhatsAppTransferStrategy(TransferStrategy):
    """
    Transfer the call from the AI to a human agent.

    Sends a POST to the Platform's /dograh-webhook/transfer endpoint.
    The Platform will:
      1. Stop the Dograh bridge audio
      2. Re-broadcast the call as a normal incoming call to online agents
      3. Mark the call row with ai_transferred = 1
    """

    async def execute_transfer(self, context: Dict[str, Any]) -> bool:
        platform_api_url = context.get("platform_api_url", "")
        webhook_secret   = context.get("webhook_secret",   "")
        wa_call_id       = context.get("wa_call_id") or context.get("call_id", "")
        run_id           = context.get("workflow_run_id")
        reason           = context.get("reason", "ai_requested")

        if not platform_api_url or not wa_call_id:
            logger.warning("[WhatsApp Transfer] Missing platform_api_url or wa_call_id — cannot transfer")
            return False

        url = platform_api_url.rstrip("/") + "/dograh-webhook/transfer"
        body = {
            "wa_call_id": wa_call_id,
            "run_id":     run_id,
            "reason":     reason,
        }

        logger.info(f"[WhatsApp Transfer] Transferring {wa_call_id} to human agents")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=body,
                    headers={
                        "X-Dograh-Key":  webhook_secret,
                        "Content-Type":  "application/json",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    ok = resp.ok
                    if not ok:
                        text = await resp.text()
                        logger.error(f"[WhatsApp Transfer] HTTP {resp.status}: {text[:200]}")
                    return ok
        except Exception as exc:
            logger.error(f"[WhatsApp Transfer] Request failed: {exc}")
            return False


class WhatsAppHangupStrategy(HangupStrategy):
    """
    End the call from the AI side.

    Sends a POST to /dograh-webhook/hangup.
    The Platform will call Meta's terminate API.
    """

    async def execute_hangup(self, context: Dict[str, Any]) -> bool:
        platform_api_url = context.get("platform_api_url", "")
        webhook_secret   = context.get("webhook_secret",   "")
        wa_call_id       = context.get("wa_call_id") or context.get("call_id", "")
        run_id           = context.get("workflow_run_id")

        if not platform_api_url or not wa_call_id:
            logger.warning("[WhatsApp Hangup] Missing platform_api_url or wa_call_id — cannot hang up")
            return False

        url = platform_api_url.rstrip("/") + "/dograh-webhook/hangup"
        body = {"wa_call_id": wa_call_id, "run_id": run_id}

        logger.info(f"[WhatsApp Hangup] Hanging up {wa_call_id}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=body,
                    headers={
                        "X-Dograh-Key":  webhook_secret,
                        "Content-Type":  "application/json",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    ok = resp.ok
                    if not ok:
                        text = await resp.text()
                        logger.error(f"[WhatsApp Hangup] HTTP {resp.status}: {text[:200]}")
                    return ok
        except Exception as exc:
            logger.error(f"[WhatsApp Hangup] Request failed: {exc}")
            return False
