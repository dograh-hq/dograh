"""Twilio-specific call operation strategies.

This module contains the business logic for Twilio call operations,
maintaining proper separation of concerns between protocol handling and business logic.
"""

from typing import Any, Dict

import aiohttp
from loguru import logger
from pipecat.serializers.call_strategies import HangupStrategy, TransferStrategy

from api.errors.failure import (
    DograhFailure,
    ErrorSource,
    ErrorType,
    classify_exception,
    classify_http_response,
    log_failure,
)


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
            from api.services.telephony.call_transfer_manager import (
                get_call_transfer_manager,
            )

            call_transfer_manager = await get_call_transfer_manager()
            return await call_transfer_manager.find_transfer_context_for_call(call_sid)

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
            account_sid = context.get("account_sid")
            auth_token = context.get("auth_token")
            call_sid = context.get("call_sid")
            region = context.get("region")
            edge = context.get("edge")

            if not account_sid or not auth_token:
                log_failure(
                    DograhFailure(
                        source=ErrorSource.TELEPHONY,
                        type=ErrorType.CONFIG_ERROR,
                        code="twilio-missing-hangup-credentials",
                        internal_message="Cannot hang up Twilio call: missing required Twilio credentials",
                        external_message="Check the Twilio credentials configured for this call.",
                        provider="twilio",
                        error_owner="user",
                        retryable=False,
                    ),
                    operation="hang up call",
                )
                return False

            if not call_sid:
                log_failure(
                    DograhFailure(
                        source=ErrorSource.TELEPHONY,
                        type=ErrorType.SYSTEM_ERROR,
                        code="twilio-missing-call-sid",
                        internal_message=(
                            "Cannot hang up Twilio call: call SID is missing from "
                            "runtime call context"
                        ),
                        external_message=(
                            "Dograh could not identify the active Twilio call. Please "
                            "retry or contact support if the problem continues."
                        ),
                        provider="twilio",
                        error_owner="operator",
                        retryable=False,
                    ),
                    operation="hang up call",
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
                        log_failure(
                            classify_http_response(
                                response.status,
                                f"Twilio hangup returned HTTP {response.status}: "
                                f"{response_text}",
                                source=ErrorSource.TELEPHONY,
                                provider="twilio",
                                error_owner="user",
                            ),
                            operation="hang up call",
                        )
                        return False

        except Exception as e:
            log_failure(
                classify_exception(
                    e,
                    source=ErrorSource.TELEPHONY,
                    provider="twilio",
                    error_owner="user",
                ),
                operation="hang up call",
            )
            return False
