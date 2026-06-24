"""Google OAuth helper: consent URL + configured gating + code exchange."""

from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from api.services.auth import google_oauth


def test_build_consent_url_has_required_params():
    with patch.object(google_oauth, "GOOGLE_CLIENT_ID", "cid123"), patch.object(
        google_oauth, "GOOGLE_REDIRECT_URI", "https://api.auto4you.in/cb"
    ):
        url = google_oauth.build_consent_url("state-abc")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["cid123"]
    assert q["redirect_uri"] == ["https://api.auto4you.in/cb"]
    assert q["state"] == ["state-abc"]
    assert q["response_type"] == ["code"]
    assert "email" in q["scope"][0]


def test_is_configured():
    with patch.object(google_oauth, "GOOGLE_CLIENT_ID", ""), patch.object(
        google_oauth, "GOOGLE_CLIENT_SECRET", ""
    ):
        assert google_oauth.is_configured() is False
    with patch.object(google_oauth, "GOOGLE_CLIENT_ID", "a"), patch.object(
        google_oauth, "GOOGLE_CLIENT_SECRET", "b"
    ):
        assert google_oauth.is_configured() is True


class _Resp:
    def __init__(self, ok, body):
        self.is_success = ok
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


class _Client:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        return _Resp(True, {"access_token": "ya29.tok"})

    async def get(self, *a, **k):
        return _Resp(True, {"email": "x@y.com", "email_verified": True, "name": "X Y"})


async def test_exchange_code_returns_userinfo():
    with patch.object(google_oauth.httpx, "AsyncClient", _Client):
        info = await google_oauth.exchange_code_for_userinfo("auth-code")
    assert info["email"] == "x@y.com" and info["name"] == "X Y"


async def test_exchange_code_token_failure_returns_none():
    class _Bad(_Client):
        async def post(self, *a, **k):
            return _Resp(False, {"error": "invalid_grant"})

    with patch.object(google_oauth.httpx, "AsyncClient", _Bad):
        assert await google_oauth.exchange_code_for_userinfo("bad") is None
