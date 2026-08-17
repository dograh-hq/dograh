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
