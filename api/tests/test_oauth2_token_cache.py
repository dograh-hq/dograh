import pytest
from unittest.mock import patch, AsyncMock
from httpx import HTTPStatusError, Request, Response

from api.utils.oauth2_token_cache import OAuth2TokenCache


@pytest.fixture
def mock_redis():
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
        mock_response.json = lambda: {
            "access_token": "new_token_456",
            "expires_in": 3600
        }
        mock_response.raise_for_status = lambda: None
        mock_httpx.post.return_value = mock_response

        token = await OAuth2TokenCache.get_valid_token(
            credential_uuid="test-uuid",
            token_url="http://test/token",
            client_id="test-client",
            client_secret="test-secret",
            scope="read write"
        )

        assert token == "new_token_456"
        mock_httpx.post.assert_called_once_with(
            "http://test/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "test-client",
                "client_secret": "test-secret",
                "scope": "read write"
            }
        )
        
        # 3600 - 300 (EXPIRY_BUFFER_SECONDS) = 3300
        mock_redis.setex.assert_called_once_with(
            name="oauth2_token:test-uuid",
            time=3300,
            value="new_token_456"
        )

    @pytest.mark.asyncio
    async def test_get_valid_token_handles_short_expiry(self, mock_redis, mock_httpx):
        mock_redis.get.return_value = None

        mock_response = AsyncMock()
        mock_response.json = lambda: {
            "access_token": "new_token_789",
            "expires_in": 60 # Less than the 300 buffer
        }
        mock_response.raise_for_status = lambda: None
        mock_httpx.post.return_value = mock_response

        token = await OAuth2TokenCache.get_valid_token(
            credential_uuid="test-uuid",
            token_url="http://test/token",
            client_id="test-client",
            client_secret="test-secret"
        )

        assert token == "new_token_789"
        # Should default to 1 second minimum TTL
        mock_redis.setex.assert_called_once_with(
            name="oauth2_token:test-uuid",
            time=1,
            value="new_token_789"
        )

    @pytest.mark.asyncio
    async def test_get_valid_token_raises_on_http_error(self, mock_redis, mock_httpx):
        mock_redis.get.return_value = None

        mock_response = AsyncMock()
        def raise_err():
            raise HTTPStatusError(
                "Bad Request",
                request=Request("POST", "http://test/token"),
                response=Response(400)
            )
        mock_response.raise_for_status = raise_err
        mock_httpx.post.return_value = mock_response

        with pytest.raises(HTTPStatusError):
            await OAuth2TokenCache.get_valid_token(
                credential_uuid="test-uuid",
                token_url="http://test/token",
                client_id="test-client",
                client_secret="test-secret"
            )

    @pytest.mark.asyncio
    async def test_get_valid_token_returns_token_even_if_cache_set_fails(self, mock_redis, mock_httpx):
        mock_redis.get.return_value = None
        mock_redis.setex.side_effect = Exception("Redis went down")

        mock_response = AsyncMock()
        mock_response.json = lambda: {
            "access_token": "new_token_abc"
        }
        mock_response.raise_for_status = lambda: None
        mock_httpx.post.return_value = mock_response

        token = await OAuth2TokenCache.get_valid_token(
            credential_uuid="test-uuid",
            token_url="http://test/token",
            client_id="test-client",
            client_secret="test-secret"
        )

        assert token == "new_token_abc"

    @pytest.mark.asyncio
    async def test_get_valid_token_fetches_if_cache_get_fails(self, mock_redis, mock_httpx):
        mock_redis.get.side_effect = Exception("Redis went down")

        mock_response = AsyncMock()
        mock_response.json = lambda: {
            "access_token": "new_token_def"
        }
        mock_response.raise_for_status = lambda: None
        mock_httpx.post.return_value = mock_response

        token = await OAuth2TokenCache.get_valid_token(
            credential_uuid="test-uuid",
            token_url="http://test/token",
            client_id="test-client",
            client_secret="test-secret"
        )

        assert token == "new_token_def"
        mock_httpx.post.assert_called_once()
        
    @pytest.mark.asyncio
    async def test_invalidate_token(self, mock_redis):
        await OAuth2TokenCache.invalidate_token("test-uuid")
        mock_redis.delete.assert_called_once_with("oauth2_token:test-uuid")
