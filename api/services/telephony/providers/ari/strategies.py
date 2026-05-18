"""ARI-specific call operation strategies.

This module contains the business logic for Asterisk ARI call operations.
"""

from typing import Any, Dict

from loguru import logger
from pipecat.serializers.call_strategies import HangupStrategy, TransferStrategy


class ARIBridgeSwapStrategy(TransferStrategy):
    """Implements bridge swap transfer for Asterisk ARI.

    This strategy handles transferring calls by swapping channels in existing
    bridges, managing transfer contexts, and publishing
    transfer completion events.
    """

    async def execute_transfer(self, context: Dict[str, Any]) -> bool:
        """Execute bridge swap transfer for Asterisk ARI."""
        try:
            import aiohttp
            import redis.asyncio as aioredis
            from aiohttp import BasicAuth

            channel_id = context["channel_id"]
            ari_endpoint = context["ari_endpoint"]
            app_name = context["app_name"]
            app_password = context["app_password"]

            if not channel_id or not ari_endpoint:
                logger.warning(
                    "Cannot execute transfer: missing channel_id or ari_endpoint"
                )
                return False

            logger.info(
                f"[ARI Transfer] Executing bridge swap for channel {channel_id}"
            )

            from api.constants import REDIS_URL
            from api.db import db_client
            from api.services.telephony.call_transfer_manager import (
                get_call_transfer_manager,
            )

            auth = BasicAuth(app_name, app_password)

            # Get call transfer manager instance
            call_transfer_manager = await get_call_transfer_manager()

            # 1. Find active transfer context for this caller channel
            transfer_context = (
                await call_transfer_manager.find_transfer_context_for_call(channel_id)
            )
            if not transfer_context:
                logger.error(
                    f"[ARI Transfer] No active transfer context found for caller {channel_id}"
                )
                return False

            logger.info(
                f"[ARI Transfer] Found transfer context: {transfer_context.transfer_id}, "
                f"destination: {transfer_context.call_sid}"
            )

            # 2. Get workflow run to find current bridge and external media channel
            redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            workflow_run_id = await redis.get(f"ari:channel:{channel_id}")
            if not workflow_run_id:
                logger.error(
                    f"[ARI Transfer] No workflow run found for caller {channel_id}"
                )
                return False

            workflow_run = await db_client.get_workflow_run_by_id(int(workflow_run_id))
            if not workflow_run or not workflow_run.gathered_context:
                logger.error(
                    f"[ARI Transfer] No workflow context found for run {workflow_run_id}"
                )
                return False

            ctx = workflow_run.gathered_context
            bridge_id = ctx.get("bridge_id")
            ext_channel_id = ctx.get("ext_channel_id")

            if not bridge_id or not ext_channel_id:
                logger.error(
                    f"[ARI Transfer] Missing bridge/external channel info: {ctx}"
                )
                return False

            destination_channel_id = transfer_context.call_sid
            if not destination_channel_id:
                logger.error(
                    f"[ARI Transfer] No destination channel in transfer context"
                )
                return False

            logger.info(
                f"[ARI Transfer] Bridge swap: bridge={bridge_id}, caller={channel_id}, "
                f"destination={destination_channel_id}, ext_media={ext_channel_id}"
            )

            # 3. Set transfer state to prevent StasisEnd auto-teardown
            workflow_run.gathered_context["transfer_state"] = "in-progress"
            await db_client.update_workflow_run(
                run_id=int(workflow_run_id),
                gathered_context=workflow_run.gathered_context,
            )
            logger.debug(
                f"[ARI Transfer] Set transfer_state=in-progress for workflow {workflow_run_id}"
            )

            # 4. Execute bridge swap operations via ARI REST API
            async with aiohttp.ClientSession() as session:
                # Add destination channel to existing bridge
                add_url = f"{ari_endpoint}/ari/bridges/{bridge_id}/addChannel"
                async with session.post(
                    add_url, auth=auth, params={"channel": destination_channel_id}
                ) as response:
                    if response.status in (200, 204):
                        logger.info(
                            f"[ARI Transfer] Added destination {destination_channel_id} to bridge {bridge_id}"
                        )
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"[ARI Transfer] Failed to add destination to bridge: {response.status} {error_text}"
                        )
                        return False

                # Remove external media channel from bridge
                remove_url = f"{ari_endpoint}/ari/bridges/{bridge_id}/removeChannel"
                async with session.post(
                    remove_url, auth=auth, params={"channel": ext_channel_id}
                ) as response:
                    if response.status in (200, 204):
                        logger.info(
                            f"[ARI Transfer] Removed external media {ext_channel_id} from bridge {bridge_id}"
                        )
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"[ARI Transfer] Failed to remove external media from bridge: {response.status} {error_text}"
                        )

                # Hang up the external media channel
                hangup_url = f"{ari_endpoint}/ari/channels/{ext_channel_id}"
                async with session.delete(hangup_url, auth=auth) as response:
                    if response.status in (200, 204):
                        logger.info(
                            f"[ARI Transfer] Hung up external media channel {ext_channel_id}"
                        )
                    elif response.status == 404:
                        logger.debug(
                            f"[ARI Transfer] External media channel {ext_channel_id} already gone"
                        )
                    else:
                        error_text = await response.text()
                        logger.warning(
                            f"[ARI Transfer] Failed to hang up external media: {response.status} {error_text}"
                        )

            logger.info(
                f"[ARI Transfer] Bridge swap completed successfully for transfer {transfer_context.transfer_id}, "
                f"caller {channel_id} connected to destination {destination_channel_id} via bridge {bridge_id}"
            )

            # 5. Clean up transfer context after successful completion

            call_transfer_manager = await get_call_transfer_manager()
            await call_transfer_manager.remove_transfer_context(
                transfer_context.transfer_id
            )
            return True

        except Exception as e:
            logger.exception(f"Failed to execute ARI transfer: {e}")
            return False


class ARIHangupStrategy(HangupStrategy):
    """Implements hangup for Asterisk ARI channels."""

    async def execute_hangup(self, context: Dict[str, Any]) -> bool:
        """Hang up the Asterisk channel via ARI REST API."""
        try:
            import aiohttp
            from aiohttp import BasicAuth

            channel_id = context["channel_id"]
            ari_endpoint = context["ari_endpoint"]
            app_name = context["app_name"]
            app_password = context["app_password"]

            if not channel_id or not ari_endpoint:
                logger.warning(
                    "Cannot hang up Asterisk channel: missing channel_id or ari_endpoint"
                )
                return False

            endpoint = f"{ari_endpoint}/ari/channels/{channel_id}"
            auth = BasicAuth(app_name, app_password)

            async with aiohttp.ClientSession() as session:
                async with session.delete(endpoint, auth=auth) as response:
                    if response.status in (200, 204):
                        logger.info(
                            f"Successfully terminated Asterisk channel {channel_id}"
                        )
                        return True
                    elif response.status == 404:
                        logger.debug(
                            f"Asterisk channel {channel_id} was already terminated"
                        )
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"Failed to terminate Asterisk channel {channel_id}: "
                            f"Status {response.status}, Response: {error_text}"
                        )
                        return False

        except Exception as e:
            logger.exception(f"Failed to hang up Asterisk channel: {e}")
            return False
