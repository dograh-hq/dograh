"""GoHighLevel CRM adapter: two-step upsert+note contract, failures, phone/note render."""

from unittest.mock import patch

from api.services.integrations.crm.base import CallLog, normalize_phone, render_call_note
from api.services.integrations.crm.providers import gohighlevel as ghl_mod
from api.services.integrations.crm.providers.gohighlevel import GoHighLevelProvider


class _Resp:
    def __init__(self, ok, status, body):
        self.is_success = ok
        self.status_code = status
        self._body = body
        self.content = b"x"

    def json(self):
        return self._body


class _Client:
    """Routes the two POSTs (upsert, note) by URL; records calls."""

    upsert = _Resp(True, 200, {"contact": {"id": "c123"}})
    note = _Resp(True, 201, {"id": "n1"})
    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        _Client.calls.append({"url": url, "headers": headers, "json": json})
        return _Client.note if url.endswith("/notes") else _Client.upsert


def _provider():
    return GoHighLevelProvider(api_key="tok", location_id="loc1")


def test_normalize_phone_india_default():
    assert normalize_phone("9876543210") == "+919876543210"
    assert normalize_phone("+91 98765 43210") == "+919876543210"
    assert normalize_phone("") == ""


def test_render_note_has_key_facts():
    note = render_call_note(
        CallLog(phone="x", disposition="INTERESTED", duration_seconds=42, recording_url="http://r")
    )
    assert "INTERESTED" in note and "42s" in note and "http://r" in note


async def test_sync_success_upserts_then_notes():
    _Client.calls = []
    _Client.upsert = _Resp(True, 200, {"contact": {"id": "c123"}})
    _Client.note = _Resp(True, 201, {"id": "n1"})
    with patch.object(ghl_mod.httpx, "AsyncClient", _Client):
        res = await _provider().sync_call(
            CallLog(phone="9876543210", name="Rahul", disposition="INTERESTED")
        )
    assert res.ok and res.contact_id == "c123"
    up = _Client.calls[0]
    assert up["url"].endswith("/contacts/upsert")
    assert up["headers"]["Authorization"] == "Bearer tok"
    assert up["headers"]["Version"] == "2021-07-28"
    assert up["json"]["locationId"] == "loc1"
    assert up["json"]["phone"] == "+919876543210"
    assert _Client.calls[1]["url"].endswith("/contacts/c123/notes")


async def test_no_phone_fails_fast():
    res = await _provider().sync_call(CallLog(phone=""))
    assert not res.ok and res.detail == "no_phone"


async def test_upsert_failure_returns_not_ok():
    _Client.upsert = _Resp(False, 401, {"message": "Invalid token"})
    with patch.object(ghl_mod.httpx, "AsyncClient", _Client):
        res = await _provider().sync_call(CallLog(phone="9876543210"))
    assert not res.ok and "Invalid token" in res.detail


async def test_note_failure_is_partial():
    _Client.upsert = _Resp(True, 200, {"contact": {"id": "c123"}})
    _Client.note = _Resp(False, 422, {"message": "bad note"})
    with patch.object(ghl_mod.httpx, "AsyncClient", _Client):
        res = await _provider().sync_call(CallLog(phone="9876543210"))
    assert not res.ok and res.contact_id == "c123" and "note_failed" in res.detail
