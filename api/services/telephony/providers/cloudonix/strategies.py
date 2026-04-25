"""Cloudonix-specific call operation strategies."""

from typing import Any, Dict

from loguru import logger

from pipecat.serializers.call_strategies import HangupStrategy


class CloudonixHangupStrategy(HangupStrategy):
    """Implements hangup for Cloudonix calls."""

    async def execute_hangup(self, context: Dict[str, Any]) -> bool:
        """Terminate a Cloudonix session via REST API.

        Note: CloudonixFrameSerializer inherits TwilioFrameSerializer and maps
        Cloudonix params to Twilio-compatible keys when building the context:
            call_id     -> call_sid
            domain_id   -> account_sid
            bearer_token -> auth_token
        """
        try:
            import aiohttp

            call_id = context.get("call_sid") or context.get("call_id")
            domain_id = context.get("account_sid") or context.get("domain_id")
            bearer_token = context.get("auth_token") or context.get("bearer_token")

            if not call_id or not domain_id or not bearer_token:
                missing = [
                    k
                    for k, v in {
                        "call_id": call_id,
                        "domain_id": domain_id,
                        "bearer_token": bearer_token,
                    }.items()
                    if not v
                ]
                logger.warning(
                    f"Cannot hang up Cloudonix call: missing required parameters: {', '.join(missing)}"
                )
                return False

            endpoint = f"https://api.cloudonix.io/customers/self/domains/{domain_id}/sessions/{call_id}"
            headers = {
                "Authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json",
            }

            logger.info(f"Terminating Cloudonix call {call_id} via DELETE {endpoint}")

            async with aiohttp.ClientSession() as session:
                async with session.delete(endpoint, headers=headers) as response:
                    status = response.status
                    response_text = await response.text()

                    if status in (200, 204, 404):
                        logger.info(
                            f"Successfully terminated Cloudonix session {call_id} "
                            f"(HTTP {status})"
                        )
                        return True
                    else:
                        logger.warning(
                            f"Unexpected response terminating Cloudonix session {call_id}: "
                            f"HTTP {status}, Response: {response_text}"
                        )
                        return False

        except Exception as e:
            logger.error(
                f"Error terminating Cloudonix call "
                f"{context.get('call_sid') or context.get('call_id')}: {e}",
                exc_info=True,
            )
            return False
