"""3CX transfer/hangup strategies.

Functionally identical to ``providers/ari/strategies.py`` — 3CX rides on
top of Asterisk so the bridge-swap and channel-delete REST calls are the
same. Duplicated rather than imported because providers/AGENTS.md forbids
cross-provider imports; a future asterisk_base extraction should
consolidate the two.
"""

from typing import Any, Dict

from loguru import logger
from pipecat.serializers.call_strategies import HangupStrategy, TransferStrategy


class ThreeCxBridgeSwapStrategy(TransferStrategy):
    """Bridge-swap transfer over the underlying Asterisk."""

    async def execute_transfer(self, context: Dict[str, Any]) -> bool:
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
                f"[3CX Transfer] Executing bridge swap for channel {channel_id}"
            )

            from api.constants import REDIS_URL
            from api.db import db_client
            from api.services.telephony.call_transfer_manager import (
                get_call_transfer_manager,
            )

            auth = BasicAuth(app_name, app_password)

            call_transfer_manager = await get_call_transfer_manager()

            transfer_context = (
                await call_transfer_manager.find_transfer_context_for_call(channel_id)
            )
            if not transfer_context:
                logger.error(
                    f"[3CX Transfer] No active transfer context found for caller {channel_id}"
                )
                return False

            redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            workflow_run_id = await redis.get(f"ari:channel:{channel_id}")
            if not workflow_run_id:
                logger.error(
                    f"[3CX Transfer] No workflow run found for caller {channel_id}"
                )
                return False

            workflow_run = await db_client.get_workflow_run_by_id(int(workflow_run_id))
            if not workflow_run or not workflow_run.gathered_context:
                logger.error(
                    f"[3CX Transfer] No workflow context for run {workflow_run_id}"
                )
                return False

            ctx = workflow_run.gathered_context
            bridge_id = ctx.get("bridge_id")
            ext_channel_id = ctx.get("ext_channel_id")

            if not bridge_id or not ext_channel_id:
                logger.error(
                    f"[3CX Transfer] Missing bridge/external channel info: {ctx}"
                )
                return False

            destination_channel_id = transfer_context.call_sid
            if not destination_channel_id:
                logger.error(
                    "[3CX Transfer] No destination channel in transfer context"
                )
                return False

            workflow_run.gathered_context["transfer_state"] = "in-progress"
            await db_client.update_workflow_run(
                run_id=int(workflow_run_id),
                gathered_context=workflow_run.gathered_context,
            )

            async with aiohttp.ClientSession() as session:
                add_url = f"{ari_endpoint}/ari/bridges/{bridge_id}/addChannel"
                async with session.post(
                    add_url, auth=auth, params={"channel": destination_channel_id}
                ) as response:
                    if response.status not in (200, 204):
                        error_text = await response.text()
                        logger.error(
                            f"[3CX Transfer] Failed to add destination to bridge: "
                            f"{response.status} {error_text}"
                        )
                        return False

                remove_url = f"{ari_endpoint}/ari/bridges/{bridge_id}/removeChannel"
                async with session.post(
                    remove_url, auth=auth, params={"channel": ext_channel_id}
                ) as response:
                    if response.status not in (200, 204):
                        error_text = await response.text()
                        logger.error(
                            f"[3CX Transfer] Failed to remove external media: "
                            f"{response.status} {error_text}"
                        )

                hangup_url = f"{ari_endpoint}/ari/channels/{ext_channel_id}"
                async with session.delete(hangup_url, auth=auth) as response:
                    if response.status not in (200, 204, 404):
                        error_text = await response.text()
                        logger.warning(
                            f"[3CX Transfer] Failed to hang up external media: "
                            f"{response.status} {error_text}"
                        )

            await call_transfer_manager.remove_transfer_context(
                transfer_context.transfer_id
            )
            return True

        except Exception as e:
            logger.exception(f"Failed to execute 3CX transfer: {e}")
            return False


class ThreeCxHangupStrategy(HangupStrategy):
    """Hang up an Asterisk channel that was bridging to the 3CX trunk."""

    async def execute_hangup(self, context: Dict[str, Any]) -> bool:
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
                    if response.status in (200, 204, 404):
                        return True
                    error_text = await response.text()
                    logger.error(
                        f"Failed to terminate channel {channel_id}: "
                        f"{response.status} {error_text}"
                    )
                    return False

        except Exception as e:
            logger.exception(f"Failed to hang up Asterisk channel: {e}")
            return False
