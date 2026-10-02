"""TwiML for introductions on separate call legs, followed by conference entry."""

from xml.etree.ElementTree import Element, SubElement, tostring

from api.services.telephony.transfer_event_protocol import (
    TransferEvent,
    TransferEventType,
)


def introduction_urls(backend: str, transfer_id: str) -> dict[str, str]:
    url = f"{backend}/api/v1/telephony/twilio/transfer-introduction/{transfer_id}"
    return {
        "Url": url,
        "Method": "POST",
        "FallbackUrl": f"{url}?skip_audio=true",
        "FallbackMethod": "POST",
    }


def introduction_twiml(conference_name: str, audio_url: str | None) -> str:
    response = Element("Response")
    if audio_url:
        SubElement(response, "Play").text = audio_url
    dial = SubElement(response, "Dial")
    SubElement(
        dial, "Conference", endConferenceOnExit="true", beep="false"
    ).text = conference_name
    # A leg enters only AFTER its own clip finishes. The first waits alone;
    # two-way audio starts once the second also completes playback and enters.
    return tostring(response, encoding="unicode")


async def caller_left(manager, transfer, provider):
    # Keep a tombstone through the callback window, including when the caller
    # disconnects before the outbound Calls response supplies its SID.
    await manager.publish_transfer_event(
        TransferEvent(
            type=TransferEventType.TRANSFER_FAILED,
            transfer_id=transfer.transfer_id,
            original_call_sid=transfer.original_call_sid,
            status="failed",
            action="transfer_failed",
            reason="caller_hangup",
        )
    )
    if transfer.call_sid:
        await provider.end_transfer_leg(transfer.call_sid)


async def process_introduction_status(manager, transfer, provider, data):
    status = data.get("CallStatus")
    previous = await manager.get_transfer_result(transfer.transfer_id)
    if previous and previous.type == TransferEventType.TRANSFER_FAILED:
        # A delayed answer after cancellation must never revive the transfer.
        # A completed callback caused by our own cancellation must not end the
        # caller, who may already have resumed talking to the agent.
        if status in ("answered", "in-progress"):
            await provider.end_transfer_leg(data["CallSid"])
        return
    if status in ("answered", "in-progress"):
        event = TransferEvent(
            type=TransferEventType.DESTINATION_ANSWERED,
            transfer_id=transfer.transfer_id,
            original_call_sid=transfer.original_call_sid,
            transfer_call_sid=data.get("CallSid"),
            conference_name=transfer.conference_name,
            status="success",
            action="destination_answered",
        )
    elif status in ("busy", "no-answer", "failed", "canceled", "completed"):
        event = TransferEvent(
            type=TransferEventType.TRANSFER_FAILED,
            transfer_id=transfer.transfer_id,
            original_call_sid=transfer.original_call_sid,
            status="failed",
            action="transfer_failed",
            reason=status,
        )
        # Dial failures arriving late cannot undo an answer. A completed call
        # still cancels the transfer, including a hangup during playback.
        await manager.publish_transfer_event(
            event, only_if_pending=status != "completed"
        )
        if status == "completed":
            # During Play the recipient hasn't joined the conference yet, so
            # endConferenceOnExit cannot remove the waiting caller.
            await provider.end_transfer_leg(transfer.original_call_sid)
        return
    else:
        return
    await manager.publish_transfer_event(event)
