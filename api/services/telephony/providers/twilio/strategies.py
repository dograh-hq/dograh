"""Twilio-specific call operation strategies.

This module contains the business logic for Twilio call operations,
maintaining proper separation of concerns between protocol handling and business logic.
"""

from typing import Any, Dict

import aiohttp
from loguru import logger

from pipecat.serializers.call_strategies import HangupStrategy, TransferStrategy


class TwilioConferenceStrategy(TransferStrategy):
    """Implements conference-based call transfer for Twilio.

    This strategy transfers calls by placing them into a Twilio conference,
    with cleanup of transfer contexts upon successful completion.
    """

    async def execute_transfer(self, context: Dict[str, Any]) -> bool:
        """Execute conference transfer for Twilio call."""
        try:
            account_sid = context["account_sid"]
            auth_token = context["auth_token"]
            call_sid = context["call_sid"]
            region = context.get("region")
            edge = context.get("edge")

            # 1. Find active transfer context for this call
            transfer_context = await self._find_transfer_context_for_call(call_sid)
            if not transfer_context:
                logger.error(
                    f"[Twilio Transfer] No active transfer context found for call {call_sid}"
                )
                return False

            logger.info(
                f"[Twilio Transfer] Found transfer context: {transfer_context.transfer_id}, "
                f"original: {transfer_context.original_call_sid}"
            )

            region_prefix = f"{region}." if region else ""
            edge_prefix = f"{edge}." if edge else ""

            # Twilio API endpoint for updating calls
            endpoint = f"https://api.{edge_prefix}{region_prefix}twilio.com/2010-04-01/Accounts/{account_sid}/Calls/{call_sid}.json"

            # Create basic auth from account_sid and auth_token
            auth = aiohttp.BasicAuth(account_sid, auth_token)

            conference_name = transfer_context.conference_name
            twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Dial>
        <Conference endConferenceOnExit="true">{conference_name}</Conference>
    </Dial>
</Response>"""

            logger.debug(
                f"[Twilio Transfer] Transferring call to conference: {conference_name}"
            )

            # 2. Make the POST request to transfer the call
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    endpoint, auth=auth, data={"Twiml": twiml}
                ) as response:
                    response_text = await response.text()

                    if response.status == 200:
                        logger.info(
                            f"[Twilio Transfer] Conference transfer completed successfully for call {call_sid}, "
                            f"joined conference {conference_name}"
                        )

                        # 3. Clean up transfer context after successful transfer
                        await self._cleanup_transfer_context(
                            transfer_context.transfer_id
                        )
                        return True
                    elif response.status == 404:
                        logger.error(
                            f"Failed to transfer Twilio call {call_sid}: Call not found (404)"
                        )
                        await self._cleanup_transfer_context(
                            transfer_context.transfer_id
                        )
                        return False
                    else:
                        logger.error(
                            f"Failed to transfer Twilio call {call_sid} to conference {conference_name}: "
                            f"Status {response.status}, Response: {response_text}"
                        )
                        await self._cleanup_transfer_context(
                            transfer_context.transfer_id
                        )
                        return False

        except Exception as e:
            logger.error(f"Failed to transfer Twilio call: {e}")
            if transfer_context:
                await self._cleanup_transfer_context(transfer_context.transfer_id)
            return False

    async def _find_transfer_context_for_call(self, call_sid: str):
        """Find the active transfer context for this call."""
        try:
            import redis.asyncio as aioredis

            from api.constants import REDIS_URL
            from api.services.telephony.transfer_event_protocol import TransferContext

            # Search Redis for transfer contexts where original_call_sid matches
            redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            transfer_keys = await redis.keys("transfer:context:*")

            for key in transfer_keys:
                try:
                    context_data = await redis.get(key)
                    if context_data:
                        context = TransferContext.from_json(context_data)
                        if context.original_call_sid == call_sid:
                            return context
                except Exception:
                    continue

            return None

        except Exception as e:
            logger.error(f"[Twilio Transfer] Error finding transfer context: {e}")
            return None

    async def _cleanup_transfer_context(self, transfer_id: str):
        """Clean up transfer context after completion or failure."""
        try:
            from api.services.telephony.call_transfer_manager import (
                get_call_transfer_manager,
            )

            call_transfer_manager = await get_call_transfer_manager()
            await call_transfer_manager.remove_transfer_context(transfer_id)
        except Exception as e:
            logger.error(f"[Twilio Transfer] Error cleaning up transfer context: {e}")


class TwilioHangupStrategy(HangupStrategy):
    """Implements hangup for Twilio calls."""

    async def execute_hangup(self, context: Dict[str, Any]) -> bool:
        """Hang up the Twilio call via REST API."""
        try:
            account_sid = context["account_sid"]
            auth_token = context["auth_token"]
            call_sid = context["call_sid"]
            region = context.get("region")
            edge = context.get("edge")

            if not account_sid or not auth_token or not call_sid:
                logger.warning(
                    "Cannot hang up Twilio call: missing required credentials or call_sid"
                )
                return False

            region_prefix = f"{region}." if region else ""
            edge_prefix = f"{edge}." if edge else ""

            endpoint = f"https://api.{edge_prefix}{region_prefix}twilio.com/2010-04-01/Accounts/{account_sid}/Calls/{call_sid}.json"
            auth = aiohttp.BasicAuth(account_sid, auth_token)

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    endpoint, auth=auth, data={"Status": "completed"}
                ) as response:
                    if response.status == 200:
                        logger.info(f"Successfully terminated Twilio call {call_sid}")
                        return True
                    elif response.status == 404:
                        logger.debug(f"Twilio call {call_sid} was already terminated")
                        return True
                    else:
                        response_text = await response.text()
                        logger.error(
                            f"Failed to terminate Twilio call {call_sid}: "
                            f"Status {response.status}, Response: {response_text}"
                        )
                        return False

        except Exception as e:
            logger.exception(f"Failed to hang up Twilio call: {e}")
            return False
