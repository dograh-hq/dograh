"""Transfer introductions on Telnyx: each leg hears the clip, then both enter a conference.

Telnyx has no markup to chain "play, then join" the way TwiML does, so each leg
gets ``playback_start`` and the bridge waits for both ``call.playback.ended``
webhooks. Whichever leg finishes second creates the conference.

- Play audio: https://developers.telnyx.com/api-reference/call-commands/play-audio-url
- Playback ended: https://developers.telnyx.com/api-reference/callbacks/call-playback-ended
"""

import aiohttp
from loguru import logger

from api.db import db_client
from api.services.telephony.factory import get_telephony_provider_for_run
from api.services.telephony.providers.telnyx.provider import TelnyxProvider
from api.services.telephony.providers.telnyx.strategies import (
    TELNYX_API_BASE,
    TelnyxConferenceStrategy,
)
from api.services.telephony.transfer_event_protocol import (
    TransferContext,
    TransferEvent,
    TransferEventType,
)

_READY_TTL_SECONDS = 300


def _key(kind: str, transfer_id: str) -> str:
    return f"transfer:telnyx:introduction_{kind}:{transfer_id}"


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }


async def play_introduction(api_key: str, call_control_id: str, audio_url: str) -> bool:
    """Start the clip on one leg. False means the leg should be treated as done."""
    endpoint = f"{TELNYX_API_BASE}/calls/{call_control_id}/actions/playback_start"
    # The clip is private and single-use, so Telnyx must not cache it.
    payload = {"audio_url": audio_url, "audio_type": "wav", "cache_audio": False}
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session,
            session.post(endpoint, json=payload, headers=_headers(api_key)) as response,
        ):
            if response.status == 200:
                return True
            logger.warning(
                "Telnyx introduction playback rejected status={}", response.status
            )
    except (aiohttp.ClientError, TimeoutError) as error:
        logger.warning(
            "Telnyx introduction playback failed error={}", type(error).__name__
        )
    return False


async def resolve_transfer_provider(
    transfer_context: TransferContext,
) -> TelnyxProvider | None:
    """Resolve the TelnyxProvider for this transfer via its workflow_run.

    Routes the lookup through ``get_telephony_provider_for_run`` so the
    right credentials are used in multi-config orgs.
    """
    workflow_run_id = transfer_context.workflow_run_id
    if not workflow_run_id:
        logger.error(
            f"TransferContext {transfer_context.transfer_id} missing "
            f"workflow_run_id; cannot resolve Telnyx provider"
        )
        return None

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.error(
            f"Workflow run {workflow_run_id} not found for transfer "
            f"{transfer_context.transfer_id}"
        )
        return None

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.error(
            f"Workflow {workflow_run.workflow_id} not found for transfer "
            f"{transfer_context.transfer_id}"
        )
        return None

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )
    if not isinstance(provider, TelnyxProvider):
        logger.error(
            f"Transfer {transfer_context.transfer_id} resolved to non-Telnyx "
            f"provider ({type(provider).__name__})"
        )
        return None

    return provider


async def leg_finished(manager, transfer: TransferContext, provider, leg: str) -> None:
    """Record that ``leg`` ("caller" or "destination") heard the clip; bridge on the second."""
    redis = await manager._get_redis()
    ready_key = _key("ready", transfer.transfer_id)
    async with redis.pipeline(transaction=True) as pipe:
        _, ready, _ = (
            await pipe.sadd(ready_key, leg)
            .scard(ready_key)
            .expire(ready_key, _READY_TTL_SECONDS)
            .execute()
        )
    if ready != 2:
        return
    # Concurrent and retried webhooks for either leg must not bridge twice.
    claimed = await redis.set(
        _key("bridge", transfer.transfer_id), "1", ex=_READY_TTL_SECONDS, nx=True
    )
    if not claimed:
        return

    try:
        joined = await _bridge(manager, transfer, provider)
    except Exception as error:  # noqa: BLE001 - a failed bridge must still free both legs
        logger.warning(
            "Telnyx introduction bridge failed error={}", type(error).__name__
        )
        joined = False
    if not joined:
        # Both parties already left the agent; nobody is left to recover with.
        await provider.end_transfer_leg(transfer.call_sid)
        await provider.end_transfer_leg(transfer.original_call_sid)
        await manager.remove_transfer_context(transfer.transfer_id)


async def _bridge(manager, transfer: TransferContext, provider) -> bool:
    conference_id = await provider.create_conference(
        seed_call_control_id=transfer.call_sid, name=transfer.conference_name
    )
    if not conference_id:
        return False
    transfer.conference_id = conference_id
    await manager.store_transfer_context(transfer)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
        return await TelnyxConferenceStrategy()._join_caller(
            session,
            _headers(provider.api_key),
            conference_id=conference_id,
            caller_call_control_id=transfer.original_call_sid,
        )


async def handle_destination_event(
    manager, transfer: TransferContext, event_type: str, call_control_id: str
) -> dict | None:
    """Destination leg of a transfer with an introduction. None falls through to the plain flow."""
    if event_type == "call.answered":
        provider = await resolve_transfer_provider(transfer)
        if not provider:
            return None
        transfer.call_sid = call_control_id
        await manager.store_transfer_context(transfer)
        outcome = await manager.publish_transfer_event(
            TransferEvent(
                type=TransferEventType.DESTINATION_ANSWERED,
                transfer_id=transfer.transfer_id,
                original_call_sid=transfer.original_call_sid,
                transfer_call_sid=call_control_id,
                conference_name=transfer.conference_name,
                status="success",
                action="destination_answered",
                message="Destination answered, playing the introduction.",
            )
        )
        if outcome and outcome.type != TransferEventType.DESTINATION_ANSWERED:
            # A late answer after a timeout or caller hangup must not revive the transfer.
            await provider.end_transfer_leg(call_control_id)
            return {"status": "success"}
        redis = await manager._get_redis()
        first_answer = await redis.set(
            _key("played", transfer.transfer_id), "1", ex=_READY_TTL_SECONDS, nx=True
        )
        if not first_answer:
            # A repeated call.answered must not replay the clip mid-conversation.
            return {"status": "success"}
        if not await play_introduction(
            provider.api_key, call_control_id, transfer.introduction_audio_url
        ):
            await leg_finished(manager, transfer, provider, "destination")
        return {"status": "success"}

    if event_type == "call.playback.ended":
        provider = await resolve_transfer_provider(transfer)
        if provider:
            await leg_finished(manager, transfer, provider, "destination")
        return {"status": "success"}

    if event_type == "call.hangup" and not transfer.conference_id:
        previous = await manager.get_transfer_result(transfer.transfer_id)
        if previous and previous.type == TransferEventType.DESTINATION_ANSWERED:
            # Hung up during the introduction, after the caller already left the agent.
            provider = await resolve_transfer_provider(transfer)
            if provider:
                await provider.end_transfer_leg(transfer.original_call_sid)
            await manager.remove_transfer_context(transfer.transfer_id)
            return {"status": "success"}
    return None


async def handle_caller_event(
    manager, event_type: str, call_control_id: str, provider
) -> None:
    """Caller leg of a transfer with an introduction, before both parties are bridged."""
    transfer = await manager.find_transfer_context_for_call(call_control_id)
    if not transfer or not transfer.introduction_audio_url or transfer.conference_id:
        return
    if event_type == "call.playback.ended":
        await leg_finished(manager, transfer, provider, "caller")
    elif event_type == "call.hangup":
        await caller_left(manager, transfer, provider)


async def caller_left(manager, transfer: TransferContext, provider) -> None:
    """Caller hung up before the bridge: fail a pending transfer and free the destination."""
    await manager.publish_transfer_event(
        TransferEvent(
            type=TransferEventType.TRANSFER_FAILED,
            transfer_id=transfer.transfer_id,
            original_call_sid=transfer.original_call_sid,
            status="failed",
            action="transfer_failed",
            reason="caller_hangup",
        ),
        only_if_pending=True,
    )
    if transfer.call_sid:
        await provider.end_transfer_leg(transfer.call_sid)
    await manager.remove_transfer_context(transfer.transfer_id)
