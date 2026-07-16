"""Cloudonix-specific call operation strategies."""

from typing import Any, Dict

from loguru import logger
from pipecat.serializers.call_strategies import HangupStrategy, TransferStrategy

from api.services.telephony.providers.cloudonix.provider import CLOUDONIX_API_BASE_URL


class CloudonixConferenceStrategy(TransferStrategy):
    """Conference-based call transfer for Cloudonix.

    Moves the original caller leg into the transfer conference by forking its
    live session onto new CXML. Cloudonix has no live-CXML push equivalent to
    Twilio's call-update; ``POST /calls/{domain}/sessions/{token}/fork`` is the
    primitive that re-runs CXML on a connected session. The destination leg was
    already dialed into the conference by ``CloudonixProvider.transfer_call``.

    The fork MUST target the Cloudonix session token, which is carried on
    ``TransferContext.original_call_sid`` (the media ``callSid`` will not
    resolve the session).
    """

    async def execute_transfer(self, context: Dict[str, Any]) -> bool:
        import aiohttp

        transfer_context = None
        try:
            # call_sid here is the serializer's session token (remapped from
            # Cloudonix call_id); use it only to locate the transfer context.
            call_sid = context.get("call_sid") or context.get("call_id")
            domain_id = context.get("account_sid") or context.get("domain_id")
            bearer_token = context.get("auth_token") or context.get("bearer_token")

            transfer_context = await self._find_transfer_context_for_call(call_sid)
            if not transfer_context:
                logger.error(
                    f"[Cloudonix Transfer] No active transfer context for call {call_sid}"
                )
                return False

            if not domain_id or not bearer_token:
                logger.error(
                    "[Cloudonix Transfer] Missing domain_id or bearer_token in context"
                )
                await self._cleanup_transfer_context(transfer_context.transfer_id)
                return False

            # Always fork the session token, never the media callSid.
            session_token = transfer_context.original_call_sid
            conference_name = transfer_context.conference_name

            endpoint = (
                f"{CLOUDONIX_API_BASE_URL}/calls/{domain_id}/sessions/"
                f"{session_token}/application"
            )
            caller_cxml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<Response><Dial>"
                f'<Conference endConferenceOnExit="true" beep="false" holdMusic="false">{conference_name}</Conference>'
                "</Dial><Hangup/></Response>"
            )
            payload = {"cxml": caller_cxml}
            headers = {
                "Authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json",
            }

            logger.info(
                f"[Cloudonix Transfer] Switching session {session_token} into "
                f"conference {conference_name}"
            )

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    endpoint, json=payload, headers=headers
                ) as response:
                    body = await response.text()
                    if response.status in (200, 202):
                        logger.info(
                            f"[Cloudonix Transfer] Session {session_token} joined "
                            f"conference {conference_name} (HTTP {response.status})"
                        )
                        await self._cleanup_transfer_context(
                            transfer_context.transfer_id
                        )
                        return True
                    logger.error(
                        f"[Cloudonix Transfer] Switch Voice Application failed for session "
                        f"{session_token}: HTTP {response.status}, body: {body}"
                    )
                    await self._cleanup_transfer_context(transfer_context.transfer_id)
                    return False

        except Exception as e:
            logger.error(f"[Cloudonix Transfer] Failed to transfer call: {e}")
            if transfer_context:
                await self._cleanup_transfer_context(transfer_context.transfer_id)
            return False

    async def _find_transfer_context_for_call(self, call_sid: str):
        try:
            from api.services.telephony.call_transfer_manager import (
                get_call_transfer_manager,
            )

            manager = await get_call_transfer_manager()
            return await manager.find_transfer_context_for_call(call_sid)
        except Exception as e:
            logger.error(f"[Cloudonix Transfer] Error finding transfer context: {e}")
            return None

    async def _cleanup_transfer_context(self, transfer_id: str):
        try:
            from api.services.telephony.call_transfer_manager import (
                get_call_transfer_manager,
            )

            manager = await get_call_transfer_manager()
            await manager.remove_transfer_context(transfer_id)
        except Exception as e:
            logger.error(
                f"[Cloudonix Transfer] Error cleaning up transfer context: {e}"
            )


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

            endpoint = f"{CLOUDONIX_API_BASE_URL}/customers/self/domains/{domain_id}/sessions/{call_id}"
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
