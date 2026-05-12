"""Telnyx-specific call operation strategies.

Caller-side leg of the conference-based transfer. The destination is already
seeded into the conference by the ``call.answered`` webhook handler (see
``providers/telnyx/routes.py``); this strategy just joins the caller into the
existing conference when the pipeline tears down with
``EndTaskReason.TRANSFER_CALL``.

API reference:
- Join a conference:
  https://developers.telnyx.com/api-reference/conference-commands/join-a-conference
- Hangup call:
  https://developers.telnyx.com/api-reference/call-commands/hangup
"""

from typing import Any, Dict

import aiohttp
from loguru import logger
from pipecat.serializers.call_strategies import HangupStrategy, TransferStrategy

TELNYX_API_BASE = "https://api.telnyx.com/v2"


class TelnyxConferenceStrategy(TransferStrategy):
    """Joins the caller leg into the conference that the webhook handler
    already created (seeded with the destination on ``call.answered``).
    """

    async def execute_transfer(self, context: Dict[str, Any]) -> bool:
        caller_call_control_id = context["call_control_id"]
        api_key = context["api_key"]

        transfer_context = await self._find_transfer_context_for_call(
            caller_call_control_id
        )
        if not transfer_context:
            logger.error(
                f"[Telnyx Transfer] No active transfer context found for "
                f"call {caller_call_control_id}"
            )
            return False

        conference_id = transfer_context.conference_id
        if not conference_id:
            logger.error(
                f"[Telnyx Transfer] Transfer context {transfer_context.transfer_id} "
                f"has no conference_id — webhook handler likely failed to seed "
                f"the destination conference."
            )
            await self._cleanup_transfer_context(transfer_context.transfer_id)
            return False

        logger.info(
            f"[Telnyx Transfer] Joining caller {caller_call_control_id} into "
            f"conference {conference_id} (transfer={transfer_context.transfer_id})"
        )

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        try:
            async with aiohttp.ClientSession() as session:
                joined = await self._join_caller(
                    session,
                    headers,
                    conference_id=conference_id,
                    caller_call_control_id=caller_call_control_id,
                )
                await self._cleanup_transfer_context(transfer_context.transfer_id)
                return joined

        except Exception as e:
            logger.error(f"[Telnyx Transfer] Failed to join caller into conference: {e}")
            await self._cleanup_transfer_context(transfer_context.transfer_id)
            return False

    async def _join_caller(
        self,
        session: aiohttp.ClientSession,
        headers: Dict[str, str],
        *,
        conference_id: str,
        caller_call_control_id: str,
    ) -> bool:
        """Join the caller leg into the conference.

        end_conference_on_exit=true so the conference tears down when the
        caller hangs up. https://developers.telnyx.com/api-reference/conference-commands/join-a-conference
        """
        endpoint = f"{TELNYX_API_BASE}/conferences/{conference_id}/actions/join"
        payload = {
            "call_control_id": caller_call_control_id,
            "end_conference_on_exit": True,
        }
        async with session.post(endpoint, json=payload, headers=headers) as response:
            body = await response.text()
            if response.status != 200:
                logger.error(
                    f"[Telnyx Transfer] Join caller {caller_call_control_id} into "
                    f"conference {conference_id} failed: "
                    f"status={response.status} body={body}"
                )
                return False
            logger.info(
                f"[Telnyx Transfer] Caller {caller_call_control_id} joined "
                f"conference {conference_id}"
            )
            return True

    async def _find_transfer_context_for_call(self, caller_call_control_id: str):
        """Find the active transfer context whose original_call_sid matches."""
        try:
            import redis.asyncio as aioredis

            from api.constants import REDIS_URL
            from api.services.telephony.transfer_event_protocol import TransferContext

            redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            transfer_keys = await redis.keys("transfer:context:*")

            for key in transfer_keys:
                try:
                    context_data = await redis.get(key)
                    if context_data:
                        context = TransferContext.from_json(context_data)
                        if context.original_call_sid == caller_call_control_id:
                            return context
                except Exception:
                    continue

            return None

        except Exception as e:
            logger.error(f"[Telnyx Transfer] Error finding transfer context: {e}")
            return None

    async def _cleanup_transfer_context(self, transfer_id: str):
        try:
            from api.services.telephony.call_transfer_manager import (
                get_call_transfer_manager,
            )

            manager = await get_call_transfer_manager()
            await manager.remove_transfer_context(transfer_id)
        except Exception as e:
            logger.error(f"[Telnyx Transfer] Error cleaning up transfer context: {e}")


class TelnyxHangupStrategy(HangupStrategy):
    """REST-API hangup for Telnyx calls.

    https://developers.telnyx.com/api-reference/call-commands/hangup
    """

    async def execute_hangup(self, context: Dict[str, Any]) -> bool:
        call_control_id = context["call_control_id"]
        api_key = context["api_key"]

        if not call_control_id or not api_key:
            logger.warning(
                "Cannot hang up Telnyx call: missing call_control_id or api_key"
            )
            return False

        endpoint = (
            f"{TELNYX_API_BASE}/calls/{call_control_id}/actions/hangup"
        )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(endpoint, headers=headers) as response:
                    if response.status == 200:
                        logger.info(
                            f"Successfully terminated Telnyx call {call_control_id}"
                        )
                        return True
                    if response.status == 422:
                        # 90018: "Call has already ended"
                        # https://developers.telnyx.com/api/errors/90018
                        try:
                            error_data = await response.json()
                            if any(
                                err.get("code") == "90018"
                                for err in error_data.get("errors", [])
                            ):
                                logger.debug(
                                    f"Telnyx call {call_control_id} was already terminated"
                                )
                                return True
                        except Exception:
                            pass
                        text = await response.text()
                        logger.error(
                            f"Failed to terminate Telnyx call {call_control_id}: "
                            f"status={response.status} body={text}"
                        )
                        return False
                    text = await response.text()
                    logger.error(
                        f"Failed to terminate Telnyx call {call_control_id}: "
                        f"status={response.status} body={text}"
                    )
                    return False

        except Exception as e:
            logger.exception(f"Failed to hang up Telnyx call: {e}")
            return False
