"""Tests for the Twilio SMS inbound channel.

Covers signature verification (fail closed), MessageSid idempotency, and the
SMS-sized reply formatting. The LLM turn itself is the text-chat engine and is
covered elsewhere; here it is faked.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from api.routes.sms import router as sms_router
from api.services.workflow import sms_conversation_service as sms_service
from api.services.workflow.sms_conversation_service import (
    SMS_TRUNCATION_HINT,
    SmsReply,
    build_twiml_response,
    parse_sms_number_map,
    split_sms_text,
)

app = FastAPI()
app.include_router(sms_router, prefix="/api/v1")
client = TestClient(app, raise_server_exceptions=False)

AUTH_TOKEN = "unit-test-token"
PUBLIC_URL = "https://voice.example.test/api/v1/sms/inbound"
PATH = "/api/v1/sms/inbound"

_PARAMS = {
    "MessageSid": "SM0000000000000000000000000000001",
    "AccountSid": "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "From": "+41790000001",
    "To": "+41795391129",
    "Body": "Hallo, ich brauche einen Termin",
}


def _sign(url: str, params: dict) -> str:
    return RequestValidator(AUTH_TOKEN).compute_signature(url, params)


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    monkeypatch.setattr("api.constants.TWILIO_IE1_AUTH_TOKEN", AUTH_TOKEN)
    monkeypatch.setattr("api.constants.SMS_INBOUND_WEBHOOK_URL", None)
    monkeypatch.setattr("api.constants.BACKEND_API_ENDPOINT", "https://voice.example.test")

    calls = []

    async def _handle(**kwargs):
        calls.append(kwargs)
        return SmsReply(texts=["Gerne, wann passt es Ihnen?"])

    monkeypatch.setattr("api.routes.sms.handle_inbound_sms", _handle)
    yield SimpleNamespace(calls=calls)


# --------------------------------------------------------------------------- #
# Signature verification
# --------------------------------------------------------------------------- #


def test_missing_signature_is_rejected(_configure):
    resp = client.post(PATH, data=_PARAMS)
    assert resp.status_code == 403
    assert _configure.calls == []


def test_wrong_signature_is_rejected(_configure):
    resp = client.post(PATH, data=_PARAMS, headers={"X-Twilio-Signature": "nope"})
    assert resp.status_code == 403
    assert _configure.calls == []


def test_signature_for_other_params_is_rejected(_configure):
    tampered = {**_PARAMS, "Body": "andere Nachricht"}
    resp = client.post(
        PATH,
        data=tampered,
        headers={"X-Twilio-Signature": _sign(PUBLIC_URL, _PARAMS)},
    )
    assert resp.status_code == 403
    assert _configure.calls == []


def test_valid_signature_against_public_url_returns_twiml(_configure):
    resp = client.post(
        PATH,
        data=_PARAMS,
        headers={"X-Twilio-Signature": _sign(PUBLIC_URL, _PARAMS)},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")
    assert resp.text.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<Message>Gerne, wann passt es Ihnen?</Message>" in resp.text
    assert len(_configure.calls) == 1
    assert _configure.calls[0]["message_sid"] == _PARAMS["MessageSid"]
    assert _configure.calls[0]["from_number"] == _PARAMS["From"]


def test_valid_signature_against_forwarded_host(monkeypatch, _configure):
    monkeypatch.setattr("api.constants.BACKEND_API_ENDPOINT", "http://localhost:8000")
    url = "https://sms.example.test/api/v1/sms/inbound"
    resp = client.post(
        PATH,
        data=_PARAMS,
        headers={
            "X-Twilio-Signature": _sign(url, _PARAMS),
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "sms.example.test",
        },
    )
    assert resp.status_code == 200


def test_explicit_webhook_url_is_preferred(monkeypatch, _configure):
    monkeypatch.setattr("api.constants.BACKEND_API_ENDPOINT", "http://localhost:8000")
    monkeypatch.setattr(
        "api.constants.SMS_INBOUND_WEBHOOK_URL", "https://cfg.example.test/x/inbound"
    )
    resp = client.post(
        PATH,
        data=_PARAMS,
        headers={
            "X-Twilio-Signature": _sign("https://cfg.example.test/x/inbound", _PARAMS)
        },
    )
    assert resp.status_code == 200


def test_missing_token_fails_closed(monkeypatch, _configure):
    monkeypatch.setattr("api.constants.TWILIO_IE1_AUTH_TOKEN", None)
    resp = client.post(
        PATH,
        data=_PARAMS,
        headers={"X-Twilio-Signature": _sign(PUBLIC_URL, _PARAMS)},
    )
    assert resp.status_code == 503
    assert _configure.calls == []


def test_empty_body_acknowledged_without_turn(_configure):
    params = {**_PARAMS, "Body": ""}
    resp = client.post(
        PATH,
        data=params,
        headers={"X-Twilio-Signature": _sign(PUBLIC_URL, params)},
    )
    assert resp.status_code == 200
    assert resp.text.endswith("<Response></Response>")
    assert _configure.calls == []


# --------------------------------------------------------------------------- #
# Idempotency (MessageSid processed once)
# --------------------------------------------------------------------------- #


@pytest.fixture
def _ledger(monkeypatch):
    rows: dict[str, SimpleNamespace] = {}
    processed = []
    finalized = []

    async def _claim(*, message_sid, from_number, to_number):
        if message_sid in rows:
            return False, rows[message_sid]
        rows[message_sid] = SimpleNamespace(
            message_sid=message_sid, status="processing", response_text=None
        )
        return True, rows[message_sid]

    async def _finalize(*, message_sid, status, response_text=None, **_kw):
        rows[message_sid].status = status
        rows[message_sid].response_text = response_text
        finalized.append((message_sid, status))

    async def _process(*, from_number, to_number, body):
        processed.append(body)
        return "Antwort des Agenten", SimpleNamespace(id=1, workflow_run_id=42)

    monkeypatch.setattr(sms_service.db_client, "claim_sms_inbound_message", _claim)
    monkeypatch.setattr(sms_service.db_client, "finalize_sms_inbound_message", _finalize)
    monkeypatch.setattr(sms_service, "_process_message", _process)
    return SimpleNamespace(rows=rows, processed=processed, finalized=finalized)


async def test_duplicate_message_sid_runs_one_turn_and_replays(_ledger):
    kwargs = dict(
        message_sid="SM-dup", from_number="+41 79 000 00 01", to_number="+41795391129",
        body="Hallo",
    )
    first = await sms_service.handle_inbound_sms(**kwargs)
    second = await sms_service.handle_inbound_sms(**kwargs)

    assert _ledger.processed == ["Hallo"]
    assert first.texts == ["Antwort des Agenten"] and not first.replayed
    assert second.texts == ["Antwort des Agenten"] and second.replayed
    assert _ledger.finalized == [("SM-dup", "done")]


async def test_duplicate_while_processing_gets_empty_reply(_ledger):
    _ledger.rows["SM-inflight"] = SimpleNamespace(
        message_sid="SM-inflight", status="processing", response_text=None
    )
    reply = await sms_service.handle_inbound_sms(
        message_sid="SM-inflight", from_number="+41790000001",
        to_number="+41795391129", body="Hallo",
    )
    assert reply.texts == [] and reply.replayed
    assert _ledger.processed == []


async def test_failed_turn_marks_ledger_failed(_ledger, monkeypatch):
    async def _boom(**_kw):
        raise RuntimeError("llm down")

    monkeypatch.setattr(sms_service, "_process_message", _boom)
    with pytest.raises(RuntimeError):
        await sms_service.handle_inbound_sms(
            message_sid="SM-fail", from_number="+41790000001",
            to_number="+41795391129", body="Hallo",
        )
    assert _ledger.finalized == [("SM-fail", "failed")]


# --------------------------------------------------------------------------- #
# Reply formatting
# --------------------------------------------------------------------------- #


def test_split_short_text_single_message():
    assert split_sms_text("Hallo Welt") == ["Hallo Welt"]
    assert split_sms_text("   ") == []


def test_split_prefers_sentence_boundaries():
    text = "Satz eins ist hier. Satz zwei folgt danach. Satz drei schliesst ab."
    chunks = split_sms_text(text, max_chars=45, max_messages=5)
    assert chunks == [
        "Satz eins ist hier. Satz zwei folgt danach.",
        "Satz drei schliesst ab.",
    ]
    assert all(len(c) <= 45 for c in chunks)


def test_split_truncates_with_hint_when_too_long():
    text = "x" * 5000
    chunks = split_sms_text(text, max_chars=100, max_messages=2)
    assert len(chunks) == 2
    assert chunks[-1].endswith(SMS_TRUNCATION_HINT)
    assert all(len(c) <= 100 for c in chunks)


def test_twiml_escapes_markup():
    twiml = build_twiml_response(["Tom & Jerry <3", "Zweite"])
    assert twiml.startswith('<?xml version="1.0" encoding="UTF-8"?><Response>')
    assert "<Message>Tom &amp; Jerry &lt;3</Message><Message>Zweite</Message>" in twiml
    assert build_twiml_response([]).endswith("<Response></Response>")


def test_number_map_parsing_normalizes_and_skips_bad_entries():
    routes = parse_sms_number_map(
        '{"+41 79 539 11 29": {"workflow_id": "1", "organization_id": 2},'
        ' "+1555": {"workflow_id": "x"}}'
    )
    assert set(routes) == {"+41795391129"}
    assert routes["+41795391129"].workflow_id == 1
    assert routes["+41795391129"].organization_id == 2
    assert parse_sms_number_map("not json") == {}
