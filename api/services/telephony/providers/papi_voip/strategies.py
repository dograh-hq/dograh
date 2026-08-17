"""Papi Voip hangup strategy."""

from typing import Any, Dict

import aiohttp
from loguru import logger
from pipecat.serializers.call_strategies import HangupStrategy


class PapiVoipHangupStrategy(HangupStrategy):
    """Hang up via ``DELETE /api/instances/:id/voice/calls/:callId``."""

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        return {"x-api-key": api_key, "apikey": api_key}

    @staticmethod
    def _active_call_id(payload: Any) -> str | None:
        """Extract the first active PAP call ID from documented response shapes."""
        if isinstance(payload, dict):
            for key in ("call_id", "callId", "id"):
                value = payload.get(key)
                if value:
                    return str(value)
            for key in ("calls", "data", "items"):
                call_id = PapiVoipHangupStrategy._active_call_id(payload.get(key))
                if call_id:
                    return call_id
        if isinstance(payload, list):
            for item in payload:
                call_id = PapiVoipHangupStrategy._active_call_id(item)
                if call_id:
                    return call_id
        return None

    async def _resolve_active_call_id(
        self, session: aiohttp.ClientSession, base_url: str, api_key: str, instance_id: str
    ) -> str | None:
        endpoint = f"{base_url}/api/instances/{instance_id}/voice/calls"
        async with session.get(endpoint, headers=self._headers(api_key)) as response:
            if response.status != 200:
                logger.error(f"[Papi Voip] active calls lookup returned {response.status}")
                return None
            payload = await response.json()
            if isinstance(payload, dict):
                logger.info(
                    f"[Papi Voip] active calls response fields: {sorted(payload.keys())}"
                )
            elif isinstance(payload, list):
                logger.info(f"[Papi Voip] active calls response contains {len(payload)} item(s)")
            return self._active_call_id(payload)

    async def execute_hangup(self, context: Dict[str, Any]) -> bool:
        call_id = context.get("call_id")
        base_url = (context.get("base_url") or "").rstrip("/")
        api_key = context.get("api_key")
        instance_id = context.get("instance_id")

        if not all([call_id, base_url, api_key, instance_id]):
            logger.warning("[Papi Voip] hangup missing call_id/base_url/api_key/instance_id")
            return False

        try:
            async with aiohttp.ClientSession() as session:
                if call_id == "active":
                    call_id = await self._resolve_active_call_id(
                        session, base_url, api_key, instance_id
                    )
                    if not call_id:
                        logger.error("[Papi Voip] unable to resolve the active call ID for hangup")
                        return False

                endpoint = f"{base_url}/api/instances/{instance_id}/voice/calls/{call_id}"
                async with session.delete(endpoint, headers=self._headers(api_key)) as response:
                    if response.status in (200, 204, 404):
                        logger.info(
                            f"[Papi Voip] hangup call_id={call_id} status={response.status}"
                        )
                        return True
                    body = await response.text()
                    logger.error(
                        f"[Papi Voip] hangup failed call_id={call_id} "
                        f"status={response.status} body={body}"
                    )
                    return False
        except Exception as e:
            logger.exception(f"[Papi Voip] hangup exception: {e}")
            return False
