"""Tests for effective_public_url — the URL used for webhook signature verification.

Behind a TLS-terminating proxy, request.url carries the internal http://
scheme. Twilio and Vobiz sign the public https:// URL, so the HMAC never
matches unless we reconstruct the canonical origin first.
"""

from unittest.mock import patch

import pytest
from starlette.requests import Request

from api.utils.telephony_helper import effective_public_url


def _make_request(
    url: str,
    headers: dict[str, str] | None = None,
) -> Request:
    """Build a minimal Starlette Request with the given URL and headers."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    scope = {
        "type": "http",
        "method": "POST",
        "path": parsed.path or "/",
        "query_string": (parsed.query or "").encode(),
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ],
        "server": (parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)),
        "scheme": parsed.scheme,
    }
    return Request(scope)


class TestEffectivePublicUrl:
    def test_backend_endpoint_overrides_request_scheme(self):
        """BACKEND_API_ENDPOINT takes priority; internal http:// becomes https://."""
        request = _make_request("http://internal:8000/telephony/inbound/run")
        with patch("api.utils.telephony_helper.BACKEND_API_ENDPOINT", "https://prod.dograh.example"):
            result = effective_public_url(request)
        assert result == "https://prod.dograh.example/telephony/inbound/run"

    def test_backend_endpoint_preserves_query_string(self):
        request = _make_request("http://internal:8000/telephony/inbound/run?foo=bar&baz=1")
        with patch("api.utils.telephony_helper.BACKEND_API_ENDPOINT", "https://prod.dograh.example"):
            result = effective_public_url(request)
        assert result == "https://prod.dograh.example/telephony/inbound/run?foo=bar&baz=1"

    def test_backend_endpoint_strips_trailing_slash(self):
        request = _make_request("http://internal:8000/telephony/inbound/run")
        with patch("api.utils.telephony_helper.BACKEND_API_ENDPOINT", "https://prod.dograh.example/"):
            result = effective_public_url(request)
        assert result == "https://prod.dograh.example/telephony/inbound/run"

    def test_forwarded_proto_upgrades_scheme_when_no_backend_endpoint(self):
        """X-Forwarded-Proto: https upgrades scheme when BACKEND_API_ENDPOINT not set."""
        request = _make_request(
            "http://internal:8000/telephony/inbound/run",
            headers={
                "x-forwarded-proto": "https",
                "x-forwarded-host": "myvoice.example.com",
            },
        )
        with patch("api.utils.telephony_helper.BACKEND_API_ENDPOINT", None):
            result = effective_public_url(request)
        assert result == "https://myvoice.example.com/telephony/inbound/run"

    def test_forwarded_proto_preserves_query_string(self):
        request = _make_request(
            "http://internal:8000/telephony/inbound/run?CallSid=CA123",
            headers={
                "x-forwarded-proto": "https",
                "x-forwarded-host": "myvoice.example.com",
            },
        )
        with patch("api.utils.telephony_helper.BACKEND_API_ENDPOINT", None):
            result = effective_public_url(request)
        assert result == "https://myvoice.example.com/telephony/inbound/run?CallSid=CA123"

    def test_host_header_used_when_no_x_forwarded_host(self):
        """Falls back to Host header if X-Forwarded-Host is absent."""
        request = _make_request(
            "http://internal:8000/telephony/inbound/run",
            headers={
                "x-forwarded-proto": "https",
                "host": "myvoice.example.com",
            },
        )
        with patch("api.utils.telephony_helper.BACKEND_API_ENDPOINT", None):
            result = effective_public_url(request)
        assert result == "https://myvoice.example.com/telephony/inbound/run"

    def test_falls_back_to_request_url_without_proxy_headers(self):
        """Local dev with no proxy: returns str(request.url) unchanged."""
        request = _make_request("http://localhost:8000/telephony/inbound/run")
        with patch("api.utils.telephony_helper.BACKEND_API_ENDPOINT", None):
            result = effective_public_url(request)
        assert result == "http://localhost:8000/telephony/inbound/run"

    def test_localhost_backend_endpoint_falls_through_to_proxy_headers(self):
        """BACKEND_API_ENDPOINT=http://localhost:8000 (local dev default) is not used
        for signature reconstruction; proxy headers take over when present."""
        request = _make_request(
            "http://internal:8000/telephony/inbound/run",
            headers={
                "x-forwarded-proto": "https",
                "x-forwarded-host": "myvoice.example.com",
            },
        )
        with patch("api.utils.telephony_helper.BACKEND_API_ENDPOINT", "http://localhost:8000"):
            result = effective_public_url(request)
        assert result == "https://myvoice.example.com/telephony/inbound/run"

    def test_backend_endpoint_takes_precedence_over_forwarded_headers(self):
        """BACKEND_API_ENDPOINT wins even when X-Forwarded-Proto is present."""
        request = _make_request(
            "http://internal:8000/telephony/inbound/run",
            headers={
                "x-forwarded-proto": "https",
                "x-forwarded-host": "other.example.com",
            },
        )
        with patch("api.utils.telephony_helper.BACKEND_API_ENDPOINT", "https://canonical.example.com"):
            result = effective_public_url(request)
        assert result == "https://canonical.example.com/telephony/inbound/run"
