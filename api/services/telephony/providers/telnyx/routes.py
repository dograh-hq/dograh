"""Telnyx telephony routes (webhooks, status callbacks, answer URLs).

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via the
provider registry — see ProviderSpec.router.
"""

import json

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.db import db_client
from api.services.telephony import ws_auth
from api.services.telephony.call_transfer_manager import get_call_transfer_manager
from api.services.telephony.factory import get_telephony_provider_for_run
from api.services.telephony.providers.telnyx.provider import (
    TelnyxProvider,
    normalize_event_type,
)
from api.services.telephony.providers.telnyx.strategies import TelnyxHangupStrategy
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)
from api.services.telephony.transfer_event_protocol import (
    TransferContext,
    TransferEvent,
    TransferEventType,
)

router = APIRouter()


# Hangup causes that signal a failed transfer attempt (vs. a successful call
# that later ended normally). Mapped to user-facing reasons published in the
# TransferEvent. Source for cause values: Telnyx call.hangup payload spec —
# https://developers.telnyx.com/api-reference/callbacks/call-hangup
# Verified-but-ignored Telnyx events: they carry no call status the processor
# models, so letting them reach _process_status_update only logs
# "Unexpected status update" and appends a junk entry to the run's callback log.
_INFORMATIONAL_EVENT_TYPES = frozenset(
    {
        "streaming.started",
        "streaming.stopped",
        "call.recording.saved",
        "call.recording.error",
        "call.recording.transcription.saved",
    }
)


_HANGUP_CAUSE_TO_REASON = {
    "busy": "busy",
    "no_answer": "no_answer",
    "timeout": "no_answer",
    "call_rejected": "call_failed",
    "unallocated_number": "call_failed",
}


@router.post("/telnyx/events/{workflow_run_id}")
async def handle_telnyx_events(
    request: Request,
    workflow_run_id: int,
):
    """Handle Telnyx Call Control webhook events.

    Telnyx sends all call lifecycle events (call.initiated, call.answered,
    call.hangup, streaming.started, streaming.stopped) as JSON POST requests.

    Invalid-signature requests are answered with the same 404 "Workflow run
    not found" as unknown run ids, deliberately: run ids are sequential
    integers, so a distinct 401 on existing runs would let unsigned callers
    enumerate which runs exist. The trade-off is that a misconfigured
    ``webhook_public_key`` presents as a 404 to the caller; the
    ``logger.warning`` below carries the real reason for operators.

    When ``TELEPHONY_WS_TOKEN_SECRET`` is configured, the events webhook URL
    minted by the provider carries an HMAC capability token in its query
    string (Telnyx preserves query strings on webhook POSTs, unlike media
    stream URLs, verified live). The token is verified before any database
    lookup, so the reject path does constant work and the run-existence
    distinction is closed for both status code and timing. With no secret
    configured, the flow falls back to the lookup-then-verify order above.
    """
    set_current_run_id(workflow_run_id)

    if ws_auth.token_configured():
        presented = request.query_params.get("token")
        if not ws_auth.verify_events_token(workflow_run_id, presented):
            logger.warning(
                f"[run {workflow_run_id}] Telnyx events request rejected: "
                f"missing or invalid capability token "
                f"(token_present={presented is not None})"
            )
            # Identical to the unknown-run response so probing run ids while
            # unauthenticated is uniform in body, status, and timing.
            raise HTTPException(status_code=404, detail="Workflow run not found")

    try:
        raw_body = (await request.body()).decode("utf-8")
    except UnicodeDecodeError:
        logger.warning(
            f"[run {workflow_run_id}] Telnyx webhook body is not valid UTF-8"
        )
        raise HTTPException(status_code=400, detail="Webhook body is not valid UTF-8")

    try:
        event_data = json.loads(raw_body)
    except json.JSONDecodeError:
        # Unauthenticated until the signature check below, so an unparseable
        # body must not surface as a 500 with a stack trace.
        logger.warning(
            f"[run {workflow_run_id}] Telnyx webhook body is not valid JSON "
            f"(body_len={len(raw_body)})"
        )
        raise HTTPException(status_code=400, detail="Webhook body is not valid JSON")

    # Extract event type from Telnyx envelope. Telnyx sometimes delivers the
    # type with underscores (``streaming_started``) instead of dots
    # (``streaming.started``); normalize so downstream comparisons match either.
    data = event_data.get("data", {})
    event_type = normalize_event_type(data.get("event_type", ""))

    logger.info(
        f"[run {workflow_run_id}] Received Telnyx event: event_type={event_type}"
    )
    logger.debug(f"[run {workflow_run_id}] Telnyx event body: {json.dumps(event_data)}")

    # Get workflow run and provider
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(f"Workflow run {workflow_run_id} not found for Telnyx event")
        raise HTTPException(status_code=404, detail="Workflow run not found")

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.warning(f"Workflow {workflow_run.workflow_id} not found")
        # Same body as the unknown-run 404 above: a run whose workflow row was
        # deleted would otherwise be distinguishable from a run that never
        # existed, re-opening the enumeration channel for that class of run.
        raise HTTPException(status_code=404, detail="Workflow run not found")

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )

    signature_valid = await provider.verify_inbound_signature(
        "", event_data, dict(request.headers), raw_body
    )
    if not signature_valid:
        logger.warning(
            f"[run {workflow_run_id}] Invalid Telnyx webhook signature "
            f"(event_type={event_type}, "
            f"timestamp={request.headers.get('telnyx-timestamp')}, "
            f"body_len={len(raw_body)})"
        )
        # Deliberately identical to the unknown-run 404 above so unsigned
        # callers cannot distinguish existing runs from missing ones. The
        # warning above keeps the real reason in server logs.
        raise HTTPException(status_code=404, detail="Workflow run not found")

    logger.debug(
        f"[run {workflow_run_id}] Telnyx webhook signature verified "
        f"(event_type={event_type})"
    )

    # Skip informational events. Streaming and recording lifecycle events
    # carry no status the processor needs, but they are still verified above
    # so unsigned callers learn nothing from them.
    #
    # Dropping them before _process_status_update also keeps the run's
    # telephony_status_callbacks list meaningful: recording callbacks are
    # delivered after the recording is written, i.e. after call.hangup, so
    # letting them through would make them the *last* entry and break
    # CampaignRunnerService._count_failed_campaign_calls, which reads
    # callbacks[-1]["status"] to decide whether a call failed.
    #
    # Event names are Telnyx's documented Call Control callbacks; there is no
    # call.recording.started in that catalog.
    if event_type in _INFORMATIONAL_EVENT_TYPES:
        logger.debug(
            f"[run {workflow_run_id}] Telnyx informational event: {event_type}"
        )
        return {"status": "success"}

    # Parse the callback data into generic format
    parsed_data = provider.parse_status_callback(event_data)

    status_update = StatusCallbackRequest(
        call_id=parsed_data["call_id"],
        status=parsed_data["status"],
        from_number=parsed_data.get("from_number"),
        to_number=parsed_data.get("to_number"),
        direction=parsed_data.get("direction"),
        duration=parsed_data.get("duration"),
        extra=parsed_data.get("extra", {}),
    )

    await _process_status_update(workflow_run_id, status_update)

    return {"status": "success"}


@router.post("/telnyx/transfer-result/{transfer_id}")
async def handle_telnyx_transfer_result(transfer_id: str, request: Request):
    """Handle Telnyx Call Control events for the transfer destination leg.

    The destination leg is dialed by :meth:`TelnyxProvider.transfer_call` with
    this URL as ``webhook_url``. Telnyx sends every event for that leg here.
    Outcomes:

    - ``call.answered``: seed a conference with the destination's live
      ``call_control_id``, stamp ``conference_id`` onto the TransferContext,
      and publish ``DESTINATION_ANSWERED`` so ``transfer_call_handler`` can
      end the pipeline. ``TelnyxConferenceStrategy`` then joins the caller
      into this conference at pipeline teardown.
    - ``call.hangup`` pre-answer (no ``conference_id`` on the context):
      publish ``TRANSFER_FAILED`` so the LLM can recover.
    - ``call.hangup`` post-answer (``conference_id`` set): the destination
      left a bridged conference; hang up the caller's leg to tear down the
      empty bridge (Telnyx's create_conference doesn't accept
      ``end_conference_on_exit`` on the seed leg).

    Event references:
        - call.answered: https://developers.telnyx.com/api-reference/callbacks/call-answered
        - call.hangup:   https://developers.telnyx.com/api-reference/callbacks/call-hangup
    """
    event_data = await request.json()
    logger.info(
        f"Telnyx transfer-result webhook (transfer_id={transfer_id}): "
        f"{json.dumps(event_data)}"
    )

    data = event_data.get("data", {})
    event_type = normalize_event_type(data.get("event_type", ""))
    payload = data.get("payload", {})
    call_control_id = payload.get("call_control_id", "")

    # Pre-answer events carry no outcome — wait for answered/hangup.
    if event_type in ("call.initiated", "call.bridging", "streaming.started"):
        return {"status": "pending"}

    call_transfer_manager = await get_call_transfer_manager()
    transfer_context = await call_transfer_manager.get_transfer_context(transfer_id)
    original_call_sid = transfer_context.original_call_sid if transfer_context else ""
    conference_name = transfer_context.conference_name if transfer_context else None

    if event_type == "call.answered":
        # Seed the conference now with the destination's live call_control_id
        # from the webhook payload. The strategy at pipeline-end then only has
        # to join the caller into this conference. Idempotent on duplicate
        # webhooks: if conference_id is already stamped on the context, skip.
        conference_id = transfer_context.conference_id if transfer_context else None
        if transfer_context and not conference_id:
            conference_id = await _seed_destination_conference(
                transfer_context=transfer_context,
                destination_call_control_id=call_control_id,
            )
            if conference_id:
                transfer_context.conference_id = conference_id
                # Refresh call_sid with the live id from the webhook — it can
                # diverge from the dial-response value once the leg is routed
                # through its post-answer POP.
                transfer_context.call_sid = call_control_id
                await call_transfer_manager.store_transfer_context(transfer_context)

        if not conference_id:
            transfer_event = TransferEvent(
                type=TransferEventType.TRANSFER_FAILED,
                transfer_id=transfer_id,
                original_call_sid=original_call_sid,
                transfer_call_sid=call_control_id,
                conference_name=conference_name,
                status="transfer_failed",
                action="transfer_failed",
                reason="conference_create_failed",
                message="Failed to bridge the transfer destination into a conference.",
                end_call=True,
            )
        else:
            transfer_event = TransferEvent(
                type=TransferEventType.DESTINATION_ANSWERED,
                transfer_id=transfer_id,
                original_call_sid=original_call_sid,
                transfer_call_sid=call_control_id,
                conference_name=conference_name,
                status="success",
                action="destination_answered",
                message="Destination answered — bridging into conference.",
            )
    elif event_type == "call.hangup":
        hangup_cause = payload.get("hangup_cause", "")

        # Post-answer hangup: the destination was already bridged into a
        # conference. Telnyx's create_conference doesn't accept
        # end_conference_on_exit, so the destination's seed leg has no
        # auto-teardown on exit. Hang up the caller explicitly so they
        # aren't left in an empty conference. No event to publish — the
        # pipeline already tore down on DESTINATION_ANSWERED.
        if transfer_context and transfer_context.conference_id:
            logger.info(
                f"Destination left conference {transfer_context.conference_id} "
                f"(transfer={transfer_id}, hangup_cause={hangup_cause}); "
                f"hanging up caller to tear down the bridge."
            )
            await _hangup_caller_leg(transfer_context)
            await call_transfer_manager.remove_transfer_context(transfer_id)
            return {"status": "success"}

        # Pre-answer hangup: destination didn't connect at all.
        reason = _HANGUP_CAUSE_TO_REASON.get(hangup_cause, "call_failed")
        transfer_event = TransferEvent(
            type=TransferEventType.TRANSFER_FAILED,
            transfer_id=transfer_id,
            original_call_sid=original_call_sid,
            transfer_call_sid=call_control_id,
            conference_name=conference_name,
            status="transfer_failed",
            action="transfer_failed",
            reason=reason,
            message=(
                f"Transfer destination did not connect (hangup_cause={hangup_cause})."
            ),
            end_call=True,
        )
    else:
        logger.debug(
            f"Telnyx transfer-result ignoring event_type={event_type} for {transfer_id}"
        )
        return {"status": "pending"}

    await call_transfer_manager.publish_transfer_event(transfer_event)
    logger.info(
        f"Published {transfer_event.type} event for transfer_id={transfer_id} "
        f"(status={transfer_event.status})"
    )

    return {"status": "success"}


async def _resolve_telnyx_provider(
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


async def _seed_destination_conference(
    *,
    transfer_context: TransferContext,
    destination_call_control_id: str,
) -> str | None:
    """Create a Telnyx conference seeded with the destination leg."""
    provider = await _resolve_telnyx_provider(transfer_context)
    if not provider:
        return None

    return await provider.create_conference(
        seed_call_control_id=destination_call_control_id,
        name=transfer_context.conference_name,
    )


async def _hangup_caller_leg(transfer_context: TransferContext) -> None:
    """Hang up the caller's leg after the destination left the conference.

    Used when ``call.hangup`` arrives on the transfer-result webhook after
    the conference was already created — Telnyx's create_conference doesn't
    accept end_conference_on_exit on the seed leg, so the caller has no
    auto-teardown when the destination leaves.

    https://developers.telnyx.com/api-reference/call-commands/hangup
    """
    provider = await _resolve_telnyx_provider(transfer_context)
    if not provider:
        return

    await TelnyxHangupStrategy().execute_hangup(
        {
            "call_control_id": transfer_context.original_call_sid,
            "api_key": provider.api_key,
        }
    )
