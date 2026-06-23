"""WhatsApp AiSensy adapter + post-call render — locks the API contract.

AiSensy: apiKey in body, campaignName binds the template, positional templateParams,
destination digits-only, fire-and-forget (2xx + success!=false == submitted).
"""

from unittest.mock import patch

from api.services.whatsapp.base import normalize_destination
from api.services.whatsapp.post_call import _render
from api.services.whatsapp.providers.aisensy import AISENSY_ENDPOINT, AiSensyProvider


class _Resp:
    def __init__(self, ok, status, body):
        self.is_success = ok
        self.status_code = status
        self._body = body
        self.content = b"x"

    def json(self):
        return self._body


class _Client:
    captured: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        _Client.captured = {"url": url, "json": json}
        return _Client._resp


def test_normalize_destination_strips_non_digits():
    assert normalize_destination("+91 73404 00524") == "917340400524"
    assert normalize_destination("919876543210") == "919876543210"


def test_render_substitutes_and_blanks_unknown():
    out = _render("Hi {{name}}, call was {{disposition}}. {{missing}}", {"name": "Rahul", "disposition": "answered"})
    assert out == "Hi Rahul, call was answered. "


async def test_aisensy_success_builds_correct_payload():
    _Client._resp = _Resp(True, 200, {"success": True, "submitted_message_id": "m1"})
    with patch("api.services.whatsapp.providers.aisensy.httpx.AsyncClient", _Client):
        res = await AiSensyProvider(api_key="KEY").send_template(
            to="+91 73404 00524",
            campaign_name="post_call_followup",
            template_params=["Rahul", "http://x/rec"],
            sender_name="auto4you",
            media_url="http://x/quote.pdf",
            media_filename="quote.pdf",
        )
    assert res.ok and res.provider_message_id == "m1"
    j = _Client.captured["json"]
    assert _Client.captured["url"] == AISENSY_ENDPOINT
    assert j["apiKey"] == "KEY"
    assert j["campaignName"] == "post_call_followup"
    assert j["destination"] == "917340400524"
    assert j["userName"] == "auto4you"
    assert j["templateParams"] == ["Rahul", "http://x/rec"]
    assert j["media"] == {"url": "http://x/quote.pdf", "filename": "quote.pdf"}


async def test_aisensy_error_returns_not_ok():
    _Client._resp = _Resp(False, 401, {"success": False, "errorMessage": "invalid apiKey"})
    with patch("api.services.whatsapp.providers.aisensy.httpx.AsyncClient", _Client):
        res = await AiSensyProvider(api_key="bad").send_template(
            to="919876543210", campaign_name="c", template_params=[], sender_name="x"
        )
    assert not res.ok and "invalid apiKey" in res.detail
