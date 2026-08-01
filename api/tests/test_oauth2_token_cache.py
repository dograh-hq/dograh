import pytest
from unittest.mock import patch, AsyncMock

from api.utils.oauth2_token_cache import OAuth2TokenCache


@pytest.fixture
def mock_ssrf_guard():
    """Bypass the SSRF URL guard for unit tests that don't test URL security."""
    with patch("api.utils.oauth2_token_cache.validate_user_configured_service_url"):
        yield


@pytest.fixture
def mock_redis():
    with patch("api.utils.oauth2_token_cache._redis_client", None):
        with patch("api.utils.oauth2_token_cache.aioredis.from_url") as mock_from_url:
            mock_client = AsyncMock()
            mock_from_url.return_value = mock_client
            yield mock_client


@pytest.fixture
def mock_httpx():
    with patch("api.utils.oauth2_token_cache.httpx.AsyncClient") as mock:
        mock_client = AsyncMock()
        mock.return_value.__aenter__.return_value = mock_client
        yield mock_client


@pytest.mark.usefixtures("mock_ssrf_guard")
class TestOAuth2TokenCache:

    @pytest.mark.asyncio
    async def test_get_valid_token_uses_cache_if_available(self, mock_redis, mock_httpx):
        mock_redis.get.return_value = "cached_token_123"

        token = await OAuth2TokenCache.get_valid_token(
            credential_uuid="test-uuid",
            token_url="http://test/token",
            client_id="test-client",
            client_secret="test-secret"
        )

        assert token == "cached_token_123"
        mock_redis.get.assert_called_once_with("oauth2_token:test-uuid")
        mock_httpx.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_valid_token_fetches_and_caches_new_token(self, mock_redis, mock_httpx):
        mock_redis.get.return_value = None

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = lambda: {
            "access_token": "new_token_456",
            "expires_in": 3600
        }
        mock_httpx.post.return_value = mock_response

        token = await OAuth2TokenCache.get_valid_token(
            credential_uuid="test-uuid",
            token_url="https://test.example.com/token",
            client_id="test-client",
            client_secret="test-secret",
            scope="read write"
        )

        assert token == "new_token_456"
        # TTL = max(30, expires_in - _EXPIRY_MARGIN) = max(30, 3600 - 60) = 3540
        mock_redis.setex.assert_called_once_with(
            "oauth2_token:test-uuid", 3540, "new_token_456"
        )

    @pytest.mark.asyncio
    async def test_get_valid_token_handles_short_expiry(self, mock_redis, mock_httpx):
        """Short-lived tokens (expires_in <= _EXPIRY_MARGIN) must NOT be cached
        to avoid returning an already-expired token."""
        mock_redis.get.return_value = None

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = lambda: {
            "access_token": "new_token_789",
            "expires_in": 60  # Less than the 60s _EXPIRY_MARGIN
        }
        mock_httpx.post.return_value = mock_response

        token = await OAuth2TokenCache.get_valid_token(
            credential_uuid="test-uuid",
            token_url="https://test.example.com/token",
            client_id="test-client",
            client_secret="test-secret"
        )

        assert token == "new_token_789"
        # net_ttl = 60 - 60 = 0 → must NOT be cached
        mock_redis.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_valid_token_omitted_expires_in_skips_cache(self, mock_redis, mock_httpx):
        """Tokens omitting expires_in are treated as having unknown lifetime and must NOT be cached."""
        mock_redis.get.return_value = None

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = lambda: {
            "access_token": "new_token_unknown_expiry",
        }
        mock_httpx.post.return_value = mock_response

        token = await OAuth2TokenCache.get_valid_token(
            credential_uuid="test-uuid",
            token_url="https://test.example.com/token",
            client_id="test-client",
            client_secret="test-secret"
        )

        assert token == "new_token_unknown_expiry"
        mock_redis.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_valid_token_raises_on_http_error(self, mock_redis, mock_httpx):
        mock_redis.get.return_value = None

        mock_response = AsyncMock()
        mock_response.status_code = 400
        mock_response.json = AsyncMock(side_effect=Exception("not json"))
        mock_response.text = "Bad Request"
        mock_httpx.post.return_value = mock_response

        with pytest.raises(ValueError, match="HTTP 400"):
            await OAuth2TokenCache.get_valid_token(
                credential_uuid="test-uuid",
                token_url="https://test.example.com/token",
                client_id="test-client",
                client_secret="test-secret"
            )

    @pytest.mark.asyncio
    async def test_get_valid_token_returns_token_even_if_cache_set_fails(self, mock_redis, mock_httpx):
        mock_redis.get.return_value = None
        mock_redis.setex.side_effect = Exception("Redis went down")

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = lambda: {
            "access_token": "new_token_abc",
            "expires_in": 3600
        }
        mock_httpx.post.return_value = mock_response

        token = await OAuth2TokenCache.get_valid_token(
            credential_uuid="test-uuid",
            token_url="https://test.example.com/token",
            client_id="test-client",
            client_secret="test-secret"
        )

        assert token == "new_token_abc"

    @pytest.mark.asyncio
    async def test_get_valid_token_fetches_if_cache_get_fails(self, mock_redis, mock_httpx):
        mock_redis.get.side_effect = Exception("Redis went down")

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = lambda: {
            "access_token": "new_token_def",
            "expires_in": 3600
        }
        mock_httpx.post.return_value = mock_response

        token = await OAuth2TokenCache.get_valid_token(
            credential_uuid="test-uuid",
            token_url="https://test.example.com/token",
            client_id="test-client",
            client_secret="test-secret"
        )

        assert token == "new_token_def"
        mock_httpx.post.assert_called_once()
        
    @pytest.mark.asyncio
    async def test_invalidate_token(self, mock_redis):
        await OAuth2TokenCache.invalidate_token("test-uuid")
        mock_redis.delete.assert_called_once_with("oauth2_token:test-uuid")


# ---------------------------------------------------------------------------
# Security: SSRF guard and HTTPS enforcement (NOT using autouse mock_ssrf_guard)
# These tests exercise the real validation path in _fetch_token so that a
# regression in the guard (e.g. dropping the ValueError wrapping) is caught.
# ---------------------------------------------------------------------------
class TestFetchTokenSecurityGuards:
    """Tests that verify security-critical rejection paths without mocking the guard."""

    @pytest.mark.asyncio
    async def test_rejects_http_token_url(self):
        """Client secret must not be sent over a plaintext HTTP connection."""
        from api.utils.oauth2_token_cache import _fetch_token

        with pytest.raises(ValueError, match="HTTPS"):
            await _fetch_token(
                client_id="id",
                client_secret="secret",
                token_url="http://example.com/token",  # HTTP, not HTTPS
                scope=None,
                audience=None,
            )

    @pytest.mark.asyncio
    async def test_rejects_localhost_token_url_via_fetch_token(self, monkeypatch):
        """In SaaS mode, _fetch_token must invoke the real SSRF guard and reject localhost."""
        monkeypatch.setattr("api.utils.url_security.DEPLOYMENT_MODE", "saas")
        from api.utils.oauth2_token_cache import _fetch_token

        with pytest.raises(ValueError, match="localhost"):
            await _fetch_token(
                client_id="id",
                client_secret="secret",
                token_url="https://localhost/token",
                scope=None,
                audience=None,
            )
