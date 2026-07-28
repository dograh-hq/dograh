"""Plivo call operations invoked when the media pipeline shuts down."""

from typing import Any, Dict

import aiohttp
from loguru import logger
from pipecat.serializers.call_strategies import HangupStrategy, TransferStrategy

from api.services.telephony.call_transfer_manager import get_call_transfer_manager
from api.utils.common import get_backend_endpoints


class PlivoConferenceStrategy(TransferStrategy):
    """Redirect the original caller to XML that joins the transfer conference.

    https://docs.plivo.com/docs/voice/api/calls#transfer-a-call
    """

    async def execute_transfer(self, context: Dict[str, Any]) -> bool:
        original_call_uuid = context["call_id"]
        auth_id = context["auth_id"]
        auth_token = context["auth_token"]

        manager = await get_call_transfer_manager()
        transfer_context = await manager.find_transfer_context_for_call(
            original_call_uuid
        )
        if not transfer_context:
            logger.error(
                f"[Plivo Transfer] No active transfer context for call "
                f"{original_call_uuid}"
            )
            return False

        backend_endpoint, _ = await get_backend_endpoints()
        conference_xml_url = (
            f"{backend_endpoint}/api/v1/telephony/plivo/transfer-xml/"
            f"{transfer_context.conference_name}/{transfer_context.transfer_id}"
            "?leg=aleg"
        )
        call_endpoint = (
            f"https://api.plivo.com/v1/Account/{auth_id}/Call/{original_call_uuid}/"
        )
        payload = {
            "legs": "aleg",
            "aleg_url": conference_xml_url,
            "aleg_method": "POST",
        }
        auth = aiohttp.BasicAuth(auth_id, auth_token)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    call_endpoint, json=payload, auth=auth
                ) as response:
                    body = await response.text()
                    if response.status not in (200, 201, 202):
                        logger.error(
                            f"[Plivo Transfer] Failed to redirect caller "
                            f"{original_call_uuid}: status={response.status} body={body}"
                        )
                        await manager.remove_transfer_context(
                            transfer_context.transfer_id
                        )
                        return False

            logger.info(
                f"[Plivo Transfer] Redirected caller {original_call_uuid} "
                f"to conference {transfer_context.conference_name}"
            )
            return True
        except Exception as exc:
            logger.error(f"[Plivo Transfer] Failed to redirect caller: {exc}")
            await manager.remove_transfer_context(transfer_context.transfer_id)
            return False


class PlivoHangupStrategy(HangupStrategy):
    """Terminate a Plivo call through the Calls REST API.

    https://docs.plivo.com/docs/voice/api/calls#hang-up-a-call
    """

    async def execute_hangup(self, context: Dict[str, Any]) -> bool:
        call_uuid = context["call_id"]
        auth_id = context["auth_id"]
        auth_token = context["auth_token"]

        if not call_uuid or not auth_id or not auth_token:
            logger.warning("Cannot hang up Plivo call: missing call ID or credentials")
            return False

        endpoint = f"https://api.plivo.com/v1/Account/{auth_id}/Call/{call_uuid}/"
        auth = aiohttp.BasicAuth(auth_id, auth_token)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(endpoint, auth=auth) as response:
                    if response.status in (204, 404):
                        return True
                    body = await response.text()
                    logger.error(
                        f"Failed to terminate Plivo call {call_uuid}: "
                        f"status={response.status} body={body}"
                    )
                    return False
        except Exception as exc:
            logger.exception(f"Failed to hang up Plivo call: {exc}")
            return False
