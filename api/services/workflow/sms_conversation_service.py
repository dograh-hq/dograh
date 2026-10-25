"""Inbound SMS (Twilio) orchestration over the text-chat session service.

The SMS channel is the embed chat without a browser: the sender's phone number
is the session key (``sms_conversations``), every SMS becomes one user turn on
the same ``PipecatEngine`` path (identical prompts + ``tool_uuids`` as voice and
web chat), and the assistant text goes back as TwiML ``<Message>`` elements.

Published definition only: runs pin ``prepare_workflow_run_inputs(...)`` with
``use_draft=False``, mirroring voice inbound and the embed widget.
"""

import asyncio
import os
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Optional
from xml.sax.saxutils import escape

from loguru import logger
from pipecat.utils.run_context import set_current_run_id

from api.constants import (
    SMS_CONVERSATION_TTL_SECONDS,
    SMS_INBOUND_NUMBER_MAP,
    SMS_MAX_MESSAGE_CHARS,
    SMS_MAX_MESSAGES_PER_REPLY,
    SMS_MAX_TURNS,
    SMS_MESSAGE_RATE_LIMIT_PER_SECOND,
    TWILIO_IE1_ACCOUNT_SID,
    TWILIO_IE1_AUTH_TOKEN,
)
from api.db import db_client
from api.db.models import SmsConversationModel, WorkflowRunTextSessionModel
from api.enums import CallType, WorkflowRunMode
from api.services.call_concurrency.rate_limiter import rate_limiter
from api.services.quota_service import authorize_workflow_run_start
from api.services.workflow.embed_text_chat_service import start_embed_text_chat
from api.services.workflow.run_creation import prepare_workflow_run_inputs
from api.services.workflow.text_chat_session_service import (
    append_text_chat_user_message,
    execute_pending_text_chat_turn,
    normalize_text_chat_session_data,
)

SMS_PROVIDER = "sms"
SMS_TRUNCATION_HINT = " … (Nachricht gekürzt)"
SMS_TURN_LIMIT_REPLY = (
    "Diese Unterhaltung hat ihre maximale Länge erreicht. "
    "Schreiben Sie mir einfach erneut, um eine neue zu beginnen."
)


class SmsRouteNotFoundError(Exception):
    """Raised when the receiving SMS number maps to no workflow/organization."""


class SmsQuotaExceededError(Exception):
    """Raised when the organization may not perform billable work right now."""


class SmsRateLimitExceededError(Exception):
    """Raised when a single conversation sends messages too quickly."""


@dataclass(frozen=True)
class SmsRoute:
    workflow_id: int
    organization_id: int


@dataclass
class SmsReply:
    """Outbound texts for one inbound SMS (already split to SMS-sized chunks)."""

    texts: list[str] = field(default_factory=list)
    # True when this reply replays a previously processed MessageSid.
    replayed: bool = False


# --------------------------------------------------------------------------- #
# Number -> workflow routing
# --------------------------------------------------------------------------- #


def normalize_phone(value: Optional[str]) -> str:
    """Strip whitespace/dashes so '+41 79 539 11 29' == '+41795391129'."""
    return re.sub(r"[\s\-()]", "", value or "")


def parse_sms_number_map(raw: str) -> dict[str, SmsRoute]:
    """Parse SMS_INBOUND_NUMBER_MAP; malformed entries are skipped, not fatal."""
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"SMS_INBOUND_NUMBER_MAP is not valid JSON: {e}")
        return {}
    routes: dict[str, SmsRoute] = {}
    if not isinstance(data, dict):
        logger.error("SMS_INBOUND_NUMBER_MAP must be a JSON object")
        return {}
    for number, target in data.items():
        try:
            routes[normalize_phone(str(number))] = SmsRoute(
                workflow_id=int(target["workflow_id"]),
                organization_id=int(target["organization_id"]),
            )
        except (KeyError, TypeError, ValueError):
            logger.error(f"SMS_INBOUND_NUMBER_MAP: invalid entry for {number!r}")
    return routes


async def resolve_sms_route(to_number: str) -> SmsRoute:
    """Resolve org + workflow for the receiving SMS number.

    1. Explicit map from SMS_INBOUND_NUMBER_MAP (number or "*" default).
    2. Fallback: the telephony phone-number registry (provider twilio), i.e. a
       number already wired for voice inbound also answers SMS.
    """
    routes = parse_sms_number_map(SMS_INBOUND_NUMBER_MAP)
    route = routes.get(normalize_phone(to_number)) or routes.get("*")
    if route:
        return route

    match = await db_client.find_inbound_route_by_called_number(
        provider=WorkflowRunMode.TWILIO.value, to_number=to_number
    )
    if match:
        config, phone_row = match
        if phone_row.inbound_workflow_id:
            return SmsRoute(
                workflow_id=phone_row.inbound_workflow_id,
                organization_id=config.organization_id,
            )
    raise SmsRouteNotFoundError(f"No SMS route for {to_number}")


# --------------------------------------------------------------------------- #
# Message handling (idempotent per MessageSid)
# --------------------------------------------------------------------------- #


async def handle_inbound_sms(
    *, message_sid: str, from_number: str, to_number: str, body: str
) -> SmsReply:
    """Process one inbound SMS exactly once and return the reply texts.

    Duplicate deliveries of the same ``MessageSid`` never start a second
    assistant turn: a finished one replays its stored reply, an in-flight one
    gets an empty reply (Twilio then simply stops retrying).
    """
    from_number = normalize_phone(from_number)
    to_number = normalize_phone(to_number)

    created, ledger = await db_client.claim_sms_inbound_message(
        message_sid=message_sid, from_number=from_number, to_number=to_number
    )
    if not created:
        logger.info(
            f"Duplicate SMS webhook for {message_sid} (status={ledger.status}); "
            "not re-processing"
        )
        if ledger.status == "done" and ledger.response_text:
            return SmsReply(texts=split_sms_text(ledger.response_text), replayed=True)
        return SmsReply(texts=[], replayed=True)

    try:
        reply_text, conversation = await _process_message(
            from_number=from_number, to_number=to_number, body=body
        )
    except Exception:
        await db_client.finalize_sms_inbound_message(
            message_sid=message_sid, status="failed"
        )
        raise

    await db_client.finalize_sms_inbound_message(
        message_sid=message_sid,
        status="done",
        response_text=reply_text,
        conversation_id=conversation.id if conversation else None,
        workflow_run_id=conversation.workflow_run_id if conversation else None,
    )
    return SmsReply(texts=split_sms_text(reply_text))


async def _process_message(
    *, from_number: str, to_number: str, body: str
) -> tuple[str, Optional[SmsConversationModel]]:
    route = await resolve_sms_route(to_number)
    now = datetime.now(UTC)
    ttl = timedelta(seconds=SMS_CONVERSATION_TTL_SECONDS)

    conversation = await db_client.get_active_sms_conversation(
        phone=from_number, to_number=to_number, now=now
    )
    text_session: Optional[WorkflowRunTextSessionModel] = None
    if conversation and conversation.workflow_run_id:
        text_session = await db_client.get_workflow_run_text_session(
            conversation.workflow_run_id, organization_id=route.organization_id
        )
        if not _session_usable(text_session, route):
            # Run was completed (agent ended, inactivity sweeper, …) or no
            # longer matches the route: retire the mapping and start fresh.
            await db_client.expire_sms_conversation(conversation.id, now=now)
            conversation, text_session = None, None

    greeting_text = ""
    if conversation is None or text_session is None:
        workflow = await db_client.get_workflow(
            route.workflow_id, organization_id=route.organization_id
        )
        if not workflow:
            raise SmsRouteNotFoundError(
                f"Workflow {route.workflow_id} not found in org {route.organization_id}"
            )
        await _ensure_quota(
            workflow_id=workflow.id,
            organization_id=route.organization_id,
            owner_user_id=workflow.user_id,
        )
        workflow_run = await _create_sms_workflow_run(
            workflow=workflow, route=route, from_number=from_number, to_number=to_number
        )
        set_current_run_id(workflow_run.id)
        # Seeds the text session and runs the agent's opening turn (same as
        # the embed widget) — the greeting rides along with the first reply.
        text_session = await start_embed_text_chat(
            workflow_id=workflow.id, run_id=workflow_run.id
        )
        greeting_text = latest_assistant_text(text_session)
        conversation = await db_client.create_sms_conversation(
            phone=from_number,
            to_number=to_number,
            workflow_id=workflow.id,
            organization_id=route.organization_id,
            workflow_run_id=workflow_run.id,
            expires_at=now + ttl,
        )
        owner_user_id = workflow.user_id
    else:
        set_current_run_id(text_session.workflow_run.id)
        if not await rate_limiter.acquire_token(
            text_session.workflow_run.id,
            SMS_MESSAGE_RATE_LIMIT_PER_SECOND,
            scope_key=f"sms:messages:{text_session.workflow_run.id}",
        ):
            raise SmsRateLimitExceededError("Too many messages")
        owner_user_id = getattr(
            getattr(text_session.workflow_run, "workflow", None), "user_id", None
        )
        await _ensure_quota(
            workflow_id=route.workflow_id,
            organization_id=route.organization_id,
            owner_user_id=owner_user_id,
            workflow_run_id=text_session.workflow_run.id,
        )

    run_id = text_session.workflow_run.id
    turns = normalize_text_chat_session_data(text_session.session_data)["turns"]
    if len(turns) >= SMS_MAX_TURNS:
        await db_client.expire_sms_conversation(conversation.id, now=now)
        return SMS_TURN_LIMIT_REPLY, conversation

    text_session = await append_text_chat_user_message(
        run_id=run_id,
        text_session=text_session,
        user_text=body,
        expected_revision=text_session.revision,
    )
    text_session = await execute_pending_text_chat_turn(
        workflow_id=route.workflow_id, run_id=run_id, text_session=text_session
    )
    answer_text = latest_assistant_text(text_session)

    after = datetime.now(UTC)
    if _run_finished(text_session):
        # The agent ended the conversation (end-call tool etc.): next SMS
        # from this sender starts a new run instead of hitting a closed one.
        await db_client.expire_sms_conversation(conversation.id, now=after)
    else:
        await db_client.touch_sms_conversation(
            conversation.id, last_activity_at=after, expires_at=after + ttl
        )

    reply_text = "\n\n".join(t for t in (greeting_text, answer_text) if t)
    return reply_text, conversation


def _session_usable(
    text_session: Optional[WorkflowRunTextSessionModel], route: SmsRoute
) -> bool:
    if not text_session or not getattr(text_session, "workflow_run", None):
        return False
    workflow_run = text_session.workflow_run
    if workflow_run.workflow_id != route.workflow_id:
        return False
    if workflow_run.mode != WorkflowRunMode.TEXTCHAT.value:
        return False
    return not _run_finished(text_session)


def _run_finished(text_session: WorkflowRunTextSessionModel) -> bool:
    if getattr(text_session.workflow_run, "is_completed", False):
        return True
    status = normalize_text_chat_session_data(text_session.session_data)["status"]
    return status == "completed"


async def _ensure_quota(
    *,
    workflow_id: int,
    organization_id: int,
    owner_user_id: Optional[int],
    workflow_run_id: Optional[int] = None,
) -> None:
    actor_user = (
        await db_client.get_user_by_id(owner_user_id) if owner_user_id else None
    )
    result = await authorize_workflow_run_start(
        workflow_id=workflow_id,
        organization_id=organization_id,
        workflow_run_id=workflow_run_id,
        actor_user=actor_user,
    )
    if not result.has_quota:
        raise SmsQuotaExceededError(result.error_message or "Quota exceeded")


SMS_CHANNEL_HINT = (
    "Dies ist eine SMS-Konversation: Der Empfaenger LIEST den Text, er hoert ihn nicht. "
    "Schreibe Datum und Uhrzeit KURZ und KOMPAKT wie \"Mo 12.10. um 13:30\" oder \"Mi 15.10. um 9:00\" - "
    "NICHT ausgeschrieben in Worten. Kuerze auch Zeitangaben aus Werkzeug-Ergebnissen beim Schreiben "
    "in diese Kurzform (Beispiel: aus \"Montag, dem zwoelften Oktober um dreizehn Uhr dreissig\" "
    "wird \"Mo 12.10. um 13:30\")."
)

async def _create_sms_workflow_run(
    *,
    workflow,
    route: SmsRoute,
    from_number,
    to_number,
    initial_context: dict | None = None,
    call_type: CallType = CallType.INBOUND,
):
    run_inputs = await prepare_workflow_run_inputs(db_client, workflow)  # published
    numeric_suffix = int(str(uuid.uuid4()).replace("-", "")[:8], 16) % 100000000
    is_outbound = call_type == CallType.OUTBOUND
    prefix = "WR-SMS-OUT-" if is_outbound else "WR-SMS-IN-"
    base_context = {
        "caller_number": from_number,
        "called_number": to_number,
        "direction": call_type.value,
        "provider": SMS_PROVIDER,
        "sms_hinweis": SMS_CHANNEL_HINT,
    }
    if initial_context:
        # Beliebige Vordaten (Patient/Termin/...) ergaenzen/ueberschreiben die
        # Standard-Felder, ohne den bestehenden Inbound-Pfad zu veraendern.
        base_context.update(initial_context)
    workflow_run = await db_client.create_workflow_run(
        name=f"{prefix}{numeric_suffix:08d}",
        workflow_id=workflow.id,
        mode=WorkflowRunMode.TEXTCHAT.value,
        user_id=workflow.user_id,
        organization_id=route.organization_id,
        call_type=call_type,
        initial_context=base_context,
        definition_id=run_inputs.definition_id,
    )
    try:
        workflow_run = await db_client.update_workflow_run(
            workflow_run.id,
            annotations={
                "sms": {
                    "source": "sms_outbound" if is_outbound else "twilio_sms",
                    "modality": "text",
                }
            },
        )
    except Exception as e:  # annotation is cosmetic; never block the reply
        logger.warning(f"Could not annotate SMS run {workflow_run.id}: {e}")
    logger.info(
        f"Created {call_type.value} SMS workflow run {workflow_run.id} "
        f"(workflow={workflow.id}, definition={run_inputs.definition_id})"
    )
    return workflow_run


async def start_outbound_sms_thread(
    *,
    workflow,
    from_number: str,
    to_number: str,
    initial_context: dict | None = None,
) -> tuple[int, bool]:
    """SMS-Thread fuer eine Zielnummer anlegen bzw. vorhandenen weiterverwenden.

    Legt einen Workflow-Run (Published-Definition, TEXTCHAT) plus die
    sms_conversations-Zuordnung "Zielnummer -> Run" an. Existiert fuer die
    Zielnummer bereits eine aktive, nutzbare Konversation auf demselben
    Workflow, wird deren Run weiterverwendet (reused=True); ein dabei
    mitgegebener initial_context wird dann NICHT uebernommen, weil der Kontext
    des Runs bereits feststeht.

    Gibt (run_id, reused) zurueck.
    """
    route = SmsRoute(
        workflow_id=workflow.id,
        organization_id=workflow.organization_id,
    )
    now = datetime.now(UTC)
    ttl = timedelta(seconds=SMS_CONVERSATION_TTL_SECONDS)

    conversation = await db_client.get_active_sms_conversation(
        phone=to_number, to_number=from_number, now=now
    )
    if conversation and conversation.workflow_run_id:
        text_session = await db_client.get_workflow_run_text_session(
            conversation.workflow_run_id, organization_id=route.organization_id
        )
        if _session_usable(text_session, route):
            logger.info(
                f"Reusing SMS conversation id={conversation.id} "
                f"run={conversation.workflow_run_id} for outbound to {to_number}"
            )
            return conversation.workflow_run_id, True
        # Veraltete Zuordnung (Run beendet / anderer Workflow): zurueckziehen.
        await db_client.expire_sms_conversation(conversation.id, now=now)

    workflow_run = await _create_sms_workflow_run(
        workflow=workflow,
        route=route,
        from_number=from_number,
        to_number=to_number,
        initial_context=initial_context,
        call_type=CallType.OUTBOUND,
    )
    set_current_run_id(workflow_run.id)
    await db_client.create_sms_conversation(
        phone=to_number,
        to_number=from_number,
        workflow_id=workflow.id,
        organization_id=route.organization_id,
        workflow_run_id=workflow_run.id,
        expires_at=now + ttl,
    )
    return workflow_run.id, False


_DSML_TOKEN_RE = re.compile(r"[<＜|｜]*[ \t]*/?[ \t]*DSML[ \t]*[|｜]?", re.IGNORECASE)
_TOOL_CALL_FRAGMENT_RE = re.compile(r"[<＜|｜]*[ \t]*/?[ \t]*(?<![A-Za-z])(?:tool)?_?calls?[ \t]*[|｜＞>]+", re.IGNORECASE)
_DSML_SUFFIXES = {w[i:] for w in ("tool_calls", "dsmltool_calls", "dsml", "tool") for i in range(len(w) - 1)}
_LEAD_FRAGMENT_RE = re.compile(r"^[ \t<＜|｜/]*([a-z_｜|＜<]{2,24})[|｜]*[>＞][ \t]*\n?")


def _strip_lead_fragment(t: str) -> str:
    """Entfernt ein fuehrendes Fragment der DSML-Token-Familie (z. B. 'alls>', '_calls>')."""
    for _ in range(2):
        m = _LEAD_FRAGMENT_RE.match(t)
        if not m:
            break
        cand = re.sub(r"[^a-z_]", "", m.group(1).lower())
        if len(cand) >= 2 and cand in _DSML_SUFFIXES:
            t = t[m.end():]
        else:
            break
    return t.lstrip()


def _clean_assistant_text(text: str) -> str:
    """Strip DeepSeek DSML tool-call token fragments that leak into the assistant text.

    Beobachtet am SMS-Rand (2026-10): '｜DSML｜tool_calls>', '_calls>', 'tool_calls>', 'alls>'.
    """
    t = text or ""
    t = _DSML_TOKEN_RE.sub("", t)
    t = _TOOL_CALL_FRAGMENT_RE.sub("", t)
    parts = [_strip_lead_fragment(seg).strip() for seg in t.split("\n\n")]
    return "\n\n".join(p for p in parts if p).strip()


def latest_assistant_text(text_session: WorkflowRunTextSessionModel) -> str:
    """Assistant text of the most recent turn (empty when none)."""
    turns = normalize_text_chat_session_data(text_session.session_data)["turns"]
    if not turns:
        return ""
    message = turns[-1].get("assistant_message") or {}
    return _clean_assistant_text(message.get("text") or "")


# --------------------------------------------------------------------------- #
# Reply formatting: SMS-sized chunks + TwiML
# --------------------------------------------------------------------------- #


def split_sms_text(
    text: str,
    *,
    max_chars: int = SMS_MAX_MESSAGE_CHARS,
    max_messages: int = SMS_MAX_MESSAGES_PER_REPLY,
) -> list[str]:
    """Split ``text`` into at most ``max_messages`` chunks of ``max_chars``.

    Prefers paragraph, then sentence, then word boundaries. Content that still
    does not fit is truncated with a visible hint instead of silently dropped.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining and len(chunks) < max_messages:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            remaining = ""
            break
        cut = _find_cut(remaining, max_chars)
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()

    if remaining:
        last = chunks[-1]
        budget = max_chars - len(SMS_TRUNCATION_HINT)
        chunks[-1] = last[: max(0, budget)].rstrip() + SMS_TRUNCATION_HINT
    return [c for c in chunks if c]


def _find_cut(text: str, limit: int) -> int:
    window = text[:limit]
    for pattern in (r"\n\s*\n", r"(?<=[.!?])\s", r"\n", r"\s"):
        matches = list(re.finditer(pattern, window))
        if matches:
            end = matches[-1].end()
            if end >= limit // 3:
                return end
    return limit


def build_twiml_response(texts: list[str]) -> str:
    """TwiML that makes Twilio send ``texts`` back to the sender, in order."""
    messages = "".join(f"<Message>{escape(t)}</Message>" for t in texts if t)
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{messages}</Response>'


# --------------------------------------------------------------------------- #
# Asynchronous reply fallback (turn exceeded Twilio's webhook deadline)
# --------------------------------------------------------------------------- #


async def send_sms_messages(*, to_number: str, from_number: str, texts: list[str]) -> bool:
    """Send ``texts`` via the Twilio REST API (region ie1 / edge dublin).

    Used only when a turn outlived the synchronous TwiML window. Returns False
    (and logs) when credentials are not configured — the reply is then lost,
    which is why SMS_SYNC_REPLY_TIMEOUT_SECONDS should stay comfortably below
    Twilio's 15 s webhook timeout.
    """
    if not texts:
        return True
    key_sid = (os.getenv("TWILIO_IE1_KEY_SID") or "").strip()
    key_secret = (os.getenv("TWILIO_IE1_KEY_SECRET") or "").strip()
    has_key_pair = bool(key_sid and key_secret)
    if not (TWILIO_IE1_ACCOUNT_SID and (TWILIO_IE1_AUTH_TOKEN or has_key_pair)):
        logger.error(
            "Late SMS reply dropped: keine Twilio-IE1-Credentials fuer den REST-Fallback "
            "(ACCOUNT_SID + AUTH_TOKEN oder KEY_PAIR fehlt)"
        )
        return False

    def _send() -> None:
        from twilio.rest import Client

        if has_key_pair:
            # Bewaehrt (Bridge, Live 2026-10-05): API-Key-Paar + Account-SID, Region ie1/dublin.
            client = Client(
                key_sid, key_secret, TWILIO_IE1_ACCOUNT_SID, region="ie1", edge="dublin"
            )
        else:
            client = Client(
                TWILIO_IE1_ACCOUNT_SID, TWILIO_IE1_AUTH_TOKEN, region="ie1", edge="dublin"
            )
        for text in texts:
            client.messages.create(to=to_number, from_=from_number, body=text)

    try:
        await asyncio.to_thread(_send)
        return True
    except Exception as e:  # noqa: BLE001 - surfaced via logs only
        logger.error(f"Late SMS reply to {to_number} failed: {type(e).__name__}: {e}")
        return False

