"""
Tests for get_backend_endpoints function in api/utils/common.py

Expected behavior:
- Output URLs must always have a scheme (http:// or https://, ws:// or wss://)
- Output URLs must NOT have trailing slashes
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.utils.common import get_backend_endpoints, get_scheme

# Valid test URLs covering various formats
possible_env_paths = [
    "http://localhost",
    "http://localhost/",
    "http://localhost:8000",
    "http://localhost:8000/",
    "http://127.0.0.1",
    "http://127.0.0.1/",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:8000/",
    "http://xyz.com",
    "http://xyz.com/",
    "https://xyz.com",
    "https://xyz.com/",
    "localhost",
    "localhost:8000",
    "localhost/",
    "localhost:8000/",
    "xyz.com",
    "xyz.com/",
    "127.0.0.1",
    "127.0.0.1/",
    "127.0.0.1:8000",
    "127.0.0.1:8000/",
]

# Invalid URLs that should raise ValueError
invalid_env_paths = [
    "http:/localhost",  # Typo: single slash in scheme
    "http:/xyz.com",  # Typo: single slash in scheme
    "https:/xyz.com",  # Typo: single slash in scheme
    "htp://xyz.com",  # Typo: missing 't' in http
    "htps://xyz.com",  # Typo: missing 't' in https
    "http//xyz.com",  # Missing colon
    "http:xyz.com",  # Missing slashes
    "http://xyz.com:abc",  # Invalid port (non-numeric)
    "http://xyz.com:-1",  # Invalid port (negative)
    "http://xyz.com:99999",  # Invalid port (out of range)
    "http://",  # Missing host
    "https://",  # Missing host
    "http://   ",  # Whitespace host
    "http://xyz .com",  # Space in hostname
    "http://xyz\t.com",  # Tab in hostname
    "http://xyz\n.com",  # Newline in hostname
    "",  # Empty string
    "   ",  # Only whitespace
]


class TestGetScheme:
    """Tests for the get_scheme helper function."""

    def test_http_scheme(self):
        assert get_scheme("http://example.com") == "http"

    def test_https_scheme(self):
        assert get_scheme("https://example.com") == "https"

    def test_no_scheme(self):
        assert get_scheme("example.com") is None
        assert get_scheme("localhost:8000") is None

    def test_malformed_url_single_slash(self):
        # 'http:/localhost' doesn't have '://' so returns None
        assert get_scheme("http:/localhost") is None

    def test_ws_scheme(self):
        assert get_scheme("ws://example.com") == "ws"
        assert get_scheme("wss://example.com") == "wss"


class TestGetBackendEndpointsWithEnvVar:
    """Tests for get_backend_endpoints when BACKEND_API_ENDPOINT is set."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "env_url,expected_http,expected_ws",
        [
            # URLs with http:// scheme (with and without trailing slash -> no trailing slash)
            ("http://xyz.com", "http://xyz.com", "ws://xyz.com"),
            ("http://xyz.com/", "http://xyz.com", "ws://xyz.com"),
            # URLs with https:// scheme (with and without trailing slash -> no trailing slash)
            ("https://xyz.com", "https://xyz.com", "wss://xyz.com"),
            ("https://xyz.com/", "https://xyz.com", "wss://xyz.com"),
            # URLs without scheme (should add http/ws, no trailing slash)
            ("xyz.com", "http://xyz.com", "ws://xyz.com"),
            ("xyz.com/", "http://xyz.com", "ws://xyz.com"),
        ],
    )
    async def test_non_localhost_urls(self, env_url, expected_http, expected_ws):
        """Test non-localhost URLs return correct http and ws endpoints."""
        with patch("api.utils.common.BACKEND_API_ENDPOINT", env_url):
            http_url, ws_url = await get_backend_endpoints()
            assert http_url == expected_http
            assert ws_url == expected_ws

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "env_url,expected_http,expected_ws",
        [
            # localhost URLs with http:// scheme
            ("http://localhost", "http://localhost", "ws://localhost"),
            ("http://localhost/", "http://localhost", "ws://localhost"),
            ("http://localhost:8000", "http://localhost:8000", "ws://localhost:8000"),
            ("http://localhost:8000/", "http://localhost:8000", "ws://localhost:8000"),
            # localhost URLs without scheme (should add http/ws)
            ("localhost", "http://localhost", "ws://localhost"),
            ("localhost/", "http://localhost", "ws://localhost"),
            ("localhost:8000", "http://localhost:8000", "ws://localhost:8000"),
            ("localhost:8000/", "http://localhost:8000", "ws://localhost:8000"),
            # 127.0.0.1 URLs with http:// scheme
            ("http://127.0.0.1", "http://127.0.0.1", "ws://127.0.0.1"),
            ("http://127.0.0.1/", "http://127.0.0.1", "ws://127.0.0.1"),
            ("http://127.0.0.1:8000", "http://127.0.0.1:8000", "ws://127.0.0.1:8000"),
            ("http://127.0.0.1:8000/", "http://127.0.0.1:8000", "ws://127.0.0.1:8000"),
            # 127.0.0.1 URLs without scheme (should add http/ws)
            ("127.0.0.1", "http://127.0.0.1", "ws://127.0.0.1"),
            ("127.0.0.1/", "http://127.0.0.1", "ws://127.0.0.1"),
            ("127.0.0.1:8000", "http://127.0.0.1:8000", "ws://127.0.0.1:8000"),
            ("127.0.0.1:8000/", "http://127.0.0.1:8000", "ws://127.0.0.1:8000"),
        ],
    )
    async def test_localhost_urls_no_tunnel(self, env_url, expected_http, expected_ws):
        """Test localhost/127.0.0.1 URLs when tunnel is not available."""
        with patch("api.utils.common.BACKEND_API_ENDPOINT", env_url):
            with patch(
                "api.utils.common.TunnelURLProvider.get_tunnel_urls",
                new_callable=AsyncMock,
            ) as mock_tunnel:
                mock_tunnel.return_value = None
                http_url, ws_url = await get_backend_endpoints()
                assert http_url == expected_http
                assert ws_url == expected_ws

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "env_url",
        [
            "http://localhost",
            "http://localhost/",
            "http://localhost:8000",
            "http://localhost:8000/",
            "localhost",
            "localhost/",
            "localhost:8000",
            "localhost:8000/",
            "http://127.0.0.1",
            "http://127.0.0.1/",
            "http://127.0.0.1:8000",
            "http://127.0.0.1:8000/",
            "127.0.0.1",
            "127.0.0.1/",
            "127.0.0.1:8000",
            "127.0.0.1:8000/",
        ],
    )
    async def test_localhost_urls_with_tunnel_available(self, env_url):
        """Test localhost/127.0.0.1 URLs prefer tunnel when available."""
        tunnel_http = "https://abc123.trycloudflare.com"
        tunnel_ws = "wss://abc123.trycloudflare.com"

        with patch("api.utils.common.BACKEND_API_ENDPOINT", env_url):
            with patch(
                "api.utils.common.TunnelURLProvider.get_tunnel_urls",
                new_callable=AsyncMock,
            ) as mock_tunnel:
                mock_tunnel.return_value = (tunnel_http, tunnel_ws)
                http_url, ws_url = await get_backend_endpoints()
                assert http_url == tunnel_http
                assert ws_url == tunnel_ws

    @pytest.mark.asyncio
    async def test_localhost_tunnel_exception_falls_back(self):
        """Test that tunnel exceptions fall back to localhost endpoint."""
        with patch("api.utils.common.BACKEND_API_ENDPOINT", "http://localhost:8000"):
            with patch(
                "api.utils.common.TunnelURLProvider.get_tunnel_urls",
                new_callable=AsyncMock,
            ) as mock_tunnel:
                mock_tunnel.side_effect = Exception("Tunnel not available")
                http_url, ws_url = await get_backend_endpoints()
                assert http_url == "http://localhost:8000"
                assert ws_url == "ws://localhost:8000"

    @pytest.mark.asyncio
    async def test_localhost_with_trailing_slash_tunnel_exception_falls_back(self):
        """Test that tunnel exceptions fall back to localhost endpoint, trailing slash stripped."""
        with patch("api.utils.common.BACKEND_API_ENDPOINT", "http://localhost:8000/"):
            with patch(
                "api.utils.common.TunnelURLProvider.get_tunnel_urls",
                new_callable=AsyncMock,
            ) as mock_tunnel:
                mock_tunnel.side_effect = Exception("Tunnel not available")
                http_url, ws_url = await get_backend_endpoints()
                assert http_url == "http://localhost:8000"
                assert ws_url == "ws://localhost:8000"

    @pytest.mark.asyncio
    async def test_127_tunnel_exception_falls_back(self):
        """Test that tunnel exceptions fall back to 127.0.0.1 endpoint."""
        with patch("api.utils.common.BACKEND_API_ENDPOINT", "http://127.0.0.1:8000"):
            with patch(
                "api.utils.common.TunnelURLProvider.get_tunnel_urls",
                new_callable=AsyncMock,
            ) as mock_tunnel:
                mock_tunnel.side_effect = Exception("Tunnel not available")
                http_url, ws_url = await get_backend_endpoints()
                assert http_url == "http://127.0.0.1:8000"
                assert ws_url == "ws://127.0.0.1:8000"

    @pytest.mark.asyncio
    async def test_127_with_trailing_slash_tunnel_exception_falls_back(self):
        """Test that tunnel exceptions fall back to 127.0.0.1 endpoint, trailing slash stripped."""
        with patch("api.utils.common.BACKEND_API_ENDPOINT", "http://127.0.0.1:8000/"):
            with patch(
                "api.utils.common.TunnelURLProvider.get_tunnel_urls",
                new_callable=AsyncMock,
            ) as mock_tunnel:
                mock_tunnel.side_effect = Exception("Tunnel not available")
                http_url, ws_url = await get_backend_endpoints()
                assert http_url == "http://127.0.0.1:8000"
                assert ws_url == "ws://127.0.0.1:8000"


class TestGetBackendEndpointsNoEnvVar:
    """Tests for get_backend_endpoints when BACKEND_API_ENDPOINT is not set."""

    @pytest.mark.asyncio
    async def test_uses_tunnel_when_no_env_var(self):
        """Test that tunnel URLs are used when env var is not set."""
        tunnel_http = "https://abc123.trycloudflare.com"
        tunnel_ws = "wss://abc123.trycloudflare.com"

        with patch("api.utils.common.BACKEND_API_ENDPOINT", None):
            with patch(
                "api.utils.common.TunnelURLProvider.get_tunnel_urls",
                new_callable=AsyncMock,
            ) as mock_tunnel:
                mock_tunnel.return_value = (tunnel_http, tunnel_ws)
                http_url, ws_url = await get_backend_endpoints()
                assert http_url == tunnel_http
                assert ws_url == tunnel_ws

    @pytest.mark.asyncio
    async def test_raises_when_no_env_var_and_no_tunnel(self):
        """Test that ValueError is raised when no env var and no tunnel."""
        with patch("api.utils.common.BACKEND_API_ENDPOINT", None):
            with patch(
                "api.utils.common.TunnelURLProvider.get_tunnel_urls",
                new_callable=AsyncMock,
            ) as mock_tunnel:
                mock_tunnel.return_value = None
                with pytest.raises(ValueError, match="No tunnel URL available"):
                    await get_backend_endpoints()


class TestSchemeMapping:
    """Tests to verify correct scheme mapping (http->ws, https->wss)."""

    @pytest.mark.asyncio
    async def test_http_maps_to_ws(self):
        """Test http:// maps to ws://"""
        with patch("api.utils.common.BACKEND_API_ENDPOINT", "http://example.com"):
            http_url, ws_url = await get_backend_endpoints()
            assert http_url == "http://example.com"
            assert ws_url == "ws://example.com"

    @pytest.mark.asyncio
    async def test_https_maps_to_wss(self):
        """Test https:// maps to wss://"""
        with patch("api.utils.common.BACKEND_API_ENDPOINT", "https://example.com"):
            http_url, ws_url = await get_backend_endpoints()
            assert http_url == "https://example.com"
            assert ws_url == "wss://example.com"

    @pytest.mark.asyncio
    async def test_no_scheme_defaults_to_http_ws(self):
        """Test URLs without scheme default to http:// and ws://"""
        with patch("api.utils.common.BACKEND_API_ENDPOINT", "example.com"):
            http_url, ws_url = await get_backend_endpoints()
            assert http_url == "http://example.com"
            assert ws_url == "ws://example.com"

    @pytest.mark.asyncio
    async def test_trailing_slash_stripped(self):
        """Test trailing slashes are stripped from output."""
        with patch("api.utils.common.BACKEND_API_ENDPOINT", "http://example.com/"):
            http_url, ws_url = await get_backend_endpoints()
            assert http_url == "http://example.com"
            assert ws_url == "ws://example.com"
            assert not http_url.endswith("/")
            assert not ws_url.endswith("/")


class TestInvalidUrls:
    """Tests for invalid URLs that should raise ValueError."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_url",
        [
            "http:/localhost",  # Typo: single slash in scheme
            "http:/xyz.com",  # Typo: single slash in scheme
            "https:/xyz.com",  # Typo: single slash in scheme
        ],
    )
    async def test_malformed_scheme_single_slash(self, invalid_url):
        """Test URLs with single slash in scheme raise ValueError."""
        with patch("api.utils.common.BACKEND_API_ENDPOINT", invalid_url):
            with patch(
                "api.utils.common.TunnelURLProvider.get_tunnel_urls",
                new_callable=AsyncMock,
            ) as mock_tunnel:
                mock_tunnel.return_value = None
                with pytest.raises(ValueError, match="Invalid BACKEND_API_ENDPOINT"):
                    await get_backend_endpoints()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_url",
        [
            "htp://xyz.com",  # Typo: missing 't' in http
            "htps://xyz.com",  # Typo: missing 't' in https
            "ftp://xyz.com",  # Unsupported scheme
            "file://xyz.com",  # Unsupported scheme
        ],
    )
    async def test_invalid_or_unsupported_scheme(self, invalid_url):
        """Test URLs with invalid or unsupported schemes raise ValueError."""
        with patch("api.utils.common.BACKEND_API_ENDPOINT", invalid_url):
            with pytest.raises(ValueError, match="Invalid BACKEND_API_ENDPOINT"):
                await get_backend_endpoints()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_url",
        [
            "http//xyz.com",  # Missing colon
            "http:xyz.com",  # Missing slashes
        ],
    )
    async def test_malformed_scheme_separator(self, invalid_url):
        """Test URLs with malformed scheme separators raise ValueError."""
        with patch("api.utils.common.BACKEND_API_ENDPOINT", invalid_url):
            with pytest.raises(ValueError, match="Invalid BACKEND_API_ENDPOINT"):
                await get_backend_endpoints()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_url",
        [
            "http://xyz.com:abc",  # Invalid port (non-numeric)
            "http://xyz.com:-1",  # Invalid port (negative)
            "http://xyz.com:99999",  # Invalid port (out of range)
            "http://xyz.com:",  # Empty port
        ],
    )
    async def test_invalid_port(self, invalid_url):
        """Test URLs with invalid ports raise ValueError."""
        with patch("api.utils.common.BACKEND_API_ENDPOINT", invalid_url):
            with pytest.raises(ValueError, match="Invalid BACKEND_API_ENDPOINT"):
                await get_backend_endpoints()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_url",
        [
            "http://",  # Missing host
            "https://",  # Missing host
        ],
    )
    async def test_missing_host(self, invalid_url):
        """Test URLs with missing host raise ValueError."""
        with patch("api.utils.common.BACKEND_API_ENDPOINT", invalid_url):
            with pytest.raises(ValueError, match="Invalid BACKEND_API_ENDPOINT"):
                await get_backend_endpoints()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_url",
        [
            "http://   ",  # Whitespace host
            "http://xyz .com",  # Space in hostname
            "http://xyz\t.com",  # Tab in hostname
            "http://xyz\n.com",  # Newline in hostname
        ],
    )
    async def test_invalid_characters_in_host(self, invalid_url):
        """Test URLs with invalid characters in hostname raise ValueError."""
        with patch("api.utils.common.BACKEND_API_ENDPOINT", invalid_url):
            with pytest.raises(ValueError, match="Invalid BACKEND_API_ENDPOINT"):
                await get_backend_endpoints()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_url",
        [
            "",  # Empty string
            "   ",  # Only whitespace
        ],
    )
    async def test_empty_or_whitespace_url(self, invalid_url):
        """Test empty or whitespace-only URLs raise ValueError."""
        with patch("api.utils.common.BACKEND_API_ENDPOINT", invalid_url):
            with pytest.raises(ValueError, match="Invalid BACKEND_API_ENDPOINT"):
                await get_backend_endpoints()
