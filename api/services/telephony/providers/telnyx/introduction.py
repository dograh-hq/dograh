"""Transfer introductions on Telnyx: each leg hears the clip, then both enter a conference.

Telnyx has no markup to chain "play, then join" the way TwiML does, so each leg
gets ``playback_start`` and the bridge waits for both ``call.playback.ended``
webhooks. Whichever leg finishes second creates the conference.

- Play audio: https://developers.telnyx.com/api-reference/call-commands/play-audio-url
- Playback ended: https://developers.telnyx.com/api-reference/callbacks/call-playback-ended
"""

import aiohttp
from loguru import logger

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


def _ready_key(transfer_id: str) -> str:
    return f"transfer:telnyx:introduction_ready:{transfer_id}"


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


async def leg_finished(manager, transfer: TransferContext, provider, leg: str) -> None:
    """Record that ``leg`` ("caller" or "destination") heard the clip; bridge on the second."""
    redis = await manager._get_redis()
    key = _ready_key(transfer.transfer_id)
    async with redis.pipeline(transaction=True) as pipe:
        added, ready, _ = (
            await pipe.sadd(key, leg)
            .scard(key)
            .expire(key, _READY_TTL_SECONDS)
            .execute()
        )
    # A retried webhook re-adds the same leg (added == 0) and must not bridge twice.
    if not added or ready != 2:
        return

    conference_id = await provider.create_conference(
        seed_call_control_id=transfer.call_sid, name=transfer.conference_name
    )
    joined = False
    if conference_id:
        transfer.conference_id = conference_id
        await manager.store_transfer_context(transfer)
        async with aiohttp.ClientSession() as session:
            joined = await TelnyxConferenceStrategy()._join_caller(
                session,
                _headers(provider.api_key),
                conference_id=conference_id,
                caller_call_control_id=transfer.original_call_sid,
            )
    if not joined:
        # Both parties already left the agent; nobody is left to recover with.
        await provider.end_transfer_leg(transfer.call_sid)
        await provider.end_transfer_leg(transfer.original_call_sid)
        await manager.remove_transfer_context(transfer.transfer_id)


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
