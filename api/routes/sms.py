"""Twilio inbound SMS webhook.

``POST /api/v1/sms/inbound`` receives Twilio's form-encoded message webhook,
verifies ``X-Twilio-Signature`` with the IE1 auth token (fail closed), runs the
message through the text-chat engine (same prompts/tools as voice + web chat)
and answers with TwiML so Twilio sends the reply SMS itself.
"""

import asyncio
from functools import partial
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request, Response
from loguru import logger
from pydantic import BaseModel
from twilio.request_validator import RequestValidator

from api import constants as app_constants
from api.db import db_client
from api.services.workflow.sms_conversation_service import (
    SmsQuotaExceededError,
    SmsRateLimitExceededError,
    SmsRouteNotFoundError,
    build_twiml_response,
    handle_inbound_sms,
    normalize_phone,
    send_sms_messages,
    start_outbound_sms_thread,
)
from api.services.workflow.text_chat_session_service import (
    TextChatPendingTurnLostError,
    TextChatSessionExecutionError,
    TextChatSessionRevisionConflictError,
)

router = APIRouter(prefix="/sms", tags=["sms"])

TWIML_MEDIA_TYPE = "application/xml"

# Turns that outlive the synchronous window keep running here; the done
# callback delivers their reply via the Twilio REST API.
_LATE_REPLY_TASKS: set[asyncio.Task] = set()


def _twiml(texts: list[str], status_code: int = 200) -> Response:
    return Response(
        content=build_twiml_response(texts),
        media_type=TWIML_MEDIA_TYPE,
        status_code=status_code,
    )


def _first_header_value(value: str | None) -> str:
    return (value or "").split(",")[0].strip()


def candidate_webhook_urls(request: Request) -> list[str]:
    """URLs Twilio may have signed, most likely first.

    The API sits behind a reverse proxy (public TLS host -> container port), so
    ``request.url`` is the internal URL. Twilio signs the public one; we try
    the configured public URL, the forwarded host, and the raw URL.
    """
    path_qs = request.url.path
    if request.url.query:
        path_qs = f"{path_qs}?{request.url.query}"

    candidates: list[str] = []
    if app_constants.SMS_INBOUND_WEBHOOK_URL:
        candidates.append(app_constants.SMS_INBOUND_WEBHOOK_URL)
    backend = (app_constants.BACKEND_API_ENDPOINT or "").rstrip("/")
    if backend:
        candidates.append(f"{backend}{path_qs}")
    forwarded_host = _first_header_value(
        request.headers.get("x-forwarded-host")
    ) or _first_header_value(request.headers.get("host"))
    forwarded_proto = (
        _first_header_value(request.headers.get("x-forwarded-proto"))
        or request.url.scheme
    )
    if forwarded_host:
        candidates.append(f"{forwarded_proto}://{forwarded_host}{path_qs}")
    candidates.append(str(request.url))

    seen: set[str] = set()
    return [c for c in candidates if not (c in seen or seen.add(c))]


def verify_twilio_signature(
    *, auth_token: str, request: Request, params: dict[str, str], signature: str
) -> bool:
    """True when ``signature`` matches any plausible public URL for this request."""
    if not signature:
        return False
    validator = RequestValidator(auth_token)
    return any(
        validator.validate(url, params, signature)
        for url in candidate_webhook_urls(request)
    )


@router.post("/inbound")
async def twilio_sms_inbound(request: Request) -> Response:
    """Twilio Messaging webhook ("A message comes in", HTTP POST)."""
    auth_token = app_constants.TWILIO_IE1_AUTH_TOKEN
    if not auth_token:
        # Fail closed: an unsigned public LLM entry point would be a cost exploit.
        logger.error("SMS inbound rejected: TWILIO_IE1_AUTH_TOKEN is not configured")
        raise HTTPException(status_code=503, detail="SMS channel not configured")

    raw_body = await request.body()
    params = dict(parse_qsl(raw_body.decode("utf-8", "replace"), keep_blank_values=True))

    signature = request.headers.get("x-twilio-signature", "")
    if not verify_twilio_signature(
        auth_token=auth_token, request=request, params=params, signature=signature
    ):
        logger.warning(
            "SMS inbound rejected: invalid or missing X-Twilio-Signature "
            f"(sid={params.get('MessageSid') or params.get('SmsSid') or '?'})"
        )
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    message_sid = params.get("MessageSid") or params.get("SmsSid") or ""
    from_number = params.get("From", "")
    to_number = params.get("To", "")
    body = (params.get("Body") or "").strip()
    if not (message_sid and from_number and to_number):
        raise HTTPException(status_code=400, detail="Missing MessageSid/From/To")
    if not body:
        # Media-only or empty SMS: acknowledge without starting a turn.
        return _twiml([])

    logger.info(
        f"SMS inbound sid={message_sid} to={to_number} chars={len(body)} "
        f"from=…{from_number[-4:]}"
    )

    task = asyncio.create_task(
        handle_inbound_sms(
            message_sid=message_sid,
            from_number=from_number,
            to_number=to_number,
            body=body,
        )
    )
    try:
        reply = await asyncio.wait_for(
            asyncio.shield(task), timeout=app_constants.SMS_SYNC_REPLY_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"SMS turn for {message_sid} exceeded the sync window; "
            "answering empty TwiML and delivering via REST when done"
        )
        _LATE_REPLY_TASKS.add(task)
        task.add_done_callback(
            partial(_deliver_late_reply, from_number=from_number, to_number=to_number)
        )
        return _twiml([])
    except SmsRouteNotFoundError as e:
        # Unknown receiving number: do not talk to strangers, just acknowledge.
        logger.warning(f"SMS inbound ignored: {e}")
        return _twiml([])
    except (SmsQuotaExceededError, SmsRateLimitExceededError) as e:
        logger.warning(f"SMS inbound throttled for {message_sid}: {e}")
        return _twiml([app_constants.SMS_ERROR_REPLY_TEXT])
    except (
        TextChatSessionRevisionConflictError,
        TextChatPendingTurnLostError,
        TextChatSessionExecutionError,
    ) as e:
        logger.error(f"SMS turn failed for {message_sid}: {e}")
        return _twiml([app_constants.SMS_ERROR_REPLY_TEXT])
    except Exception as e:  # noqa: BLE001 - public surface, generic reply
        logger.exception(f"SMS inbound failed for {message_sid}: {e}")
        return _twiml([app_constants.SMS_ERROR_REPLY_TEXT])

    return _twiml(reply.texts)


def _deliver_late_reply(task: asyncio.Task, *, from_number: str, to_number: str) -> None:
    _LATE_REPLY_TASKS.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error(f"Late SMS turn failed: {type(error).__name__}: {error}")
        texts = [app_constants.SMS_ERROR_REPLY_TEXT]
    else:
        texts = task.result().texts
    if not texts:
        return
    # Reply goes back to the sender, from the number they wrote to.
    asyncio.get_event_loop().create_task(
        send_sms_messages(to_number=from_number, from_number=to_number, texts=texts)
    )


# --------------------------------------------------------------------------- #
# Programmatischer Outbound-Start (interner Endpoint POST /api/v1/sms/outbound)
# --------------------------------------------------------------------------- #


class SmsOutboundRequest(BaseModel):
    workflow_id: int
    to_number: str
    from_number: str
    initial_context: dict | None = None
    message: str | None = None
    dry_run: bool = True


class SmsOutboundResponse(BaseModel):
    run_id: int
    sent: bool
    dry_run: bool


@router.post("/outbound", response_model=SmsOutboundResponse)
async def sms_outbound(payload: SmsOutboundRequest) -> SmsOutboundResponse:
    """Startet einen SMS-Thread zu einer Zielnummer (interner Aufruf).

    Legt den Workflow-Run samt Nummer->Run-Zuordnung an (bzw. verwendet eine
    vorhandene Session weiter) und speichert beliebige Vordaten in
    initial_context. Mit dry_run=true (Default) wird keinerlei SMS versendet;
    nur der Run wird angelegt. Ohne message wird nie gesendet.
    """
    workflow = await db_client.get_workflow_by_id(payload.workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    status_value = getattr(workflow.status, "value", workflow.status)
    if status_value != "active":
        raise HTTPException(status_code=409, detail="Workflow is not active")

    from_number = normalize_phone(payload.from_number)
    to_number = normalize_phone(payload.to_number)
    if not from_number or not to_number:
        raise HTTPException(status_code=400, detail="from_number/to_number required")

    run_id, reused = await start_outbound_sms_thread(
        workflow=workflow,
        from_number=from_number,
        to_number=to_number,
        initial_context=payload.initial_context,
    )

    sent = False
    if payload.message and not payload.dry_run:
        sent = await send_sms_messages(
            to_number=to_number, from_number=from_number, texts=[payload.message]
        )

    logger.info(
        f"SMS outbound workflow_id={workflow.id} run={run_id} "
        f"dry_run={payload.dry_run} sent={sent} reused={reused} "
        f"to=...{to_number[-4:]}"
    )
    return SmsOutboundResponse(run_id=run_id, sent=sent, dry_run=payload.dry_run)
