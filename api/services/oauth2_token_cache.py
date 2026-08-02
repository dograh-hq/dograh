"""Redis-backed cache for OAuth2 client credential tokens.

Key:   oauth2_token:<credential_uuid>
Value: raw access_token string
TTL:   max(30, expires_in - 60) seconds

Spec §5.2 interface: module-level functions get_or_fetch_token() and invalidate_token().
"""

import asyncio
import time
from typing import Optional

import cachetools
import httpx
import redis.asyncio as aioredis
from loguru import logger

from api.constants import REDIS_URL
from api.utils.url_security import (
    get_pinned_httpx_transport,
    validate_user_configured_service_url,
)

_MIN_TTL = 30  # Floor: never cache for less than 30s
_EXPIRY_MARGIN = 60  # Pre-expire: refresh 60s before real expiry
_FETCH_TIMEOUT = 10.0  # Token endpoint timeout in seconds
_locks: cachetools.TTLCache[str, asyncio.Lock] = cachetools.TTLCache(
    maxsize=1000, ttl=3600
)
_redis_client: Optional[aioredis.Redis] = None


def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
    return _redis_client


async def close_redis() -> None:
    """Close the global Redis client pool."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None


def _get_lock(credential_uuid: str) -> asyncio.Lock:
    if credential_uuid not in _locks:
        _locks[credential_uuid] = asyncio.Lock()
    return _locks[credential_uuid]


async def get_or_fetch_token(
    credential_uuid: str,
    client_id: str,
    client_secret: str,
    token_url: str,
    scope: Optional[str] = None,
    audience: Optional[str] = None,
    force_refresh: bool = False,
) -> str:
    """Return a valid access token, using Redis cache when possible.

    Args:
        force_refresh: If True, bypass the cache and force a new token fetch.

    Raises:
        ValueError: If the token fetch fails for any reason.
    """
    cache_key = f"oauth2_token:{credential_uuid}"
    fetch_start = time.time()

    if not force_refresh:
        try:
            redis_client = _get_redis()
            cached = await redis_client.get(cache_key)
            if cached:
                logger.debug(
                    f"Using cached OAuth2 token for credential {credential_uuid}"
                )
                return cached
        except Exception as exc:
            # Redis unavailable: skip cache, fetch fresh. Never crash the call.
            logger.warning(
                f"OAuth2 token cache read failed for {credential_uuid}: {exc}"
            )

    async with _get_lock(credential_uuid):
        if not force_refresh:
            # Recheck cache after acquiring lock to prevent stampeding
            try:
                redis_client = _get_redis()
                cached = await redis_client.get(cache_key)
                if cached:
                    logger.debug(
                        f"Using cached OAuth2 token for credential {credential_uuid} on recheck"
                    )
                    return cached
            except Exception:
                pass

        # Cache miss (or Redis down, or force_refresh) — fetch a fresh token.
        logger.info(f"Fetching new OAuth2 token for credential {credential_uuid}")
        token, expires_in = await _fetch_token(
            client_id=client_id,
            client_secret=client_secret,
            token_url=token_url,
            scope=scope,
            audience=audience,
        )

        # Write to cache if Redis is available.
        # Only cache when the token will still be valid after our pre-expiry margin.
        # Short-lived tokens (expires_in <= _EXPIRY_MARGIN) must not be cached.
        net_ttl = expires_in - _EXPIRY_MARGIN
        if net_ttl > 0:
            cache_ttl = int(net_ttl)
            try:
                redis_client = _get_redis()
                lua_script = """
                local invalid_key = KEYS[1]
                local cache_key = KEYS[2]
                local fetch_start = tonumber(ARGV[1])
                local cache_ttl = tonumber(ARGV[2])
                local token_val = ARGV[3]

                local invalid_raw = redis.call('GET', invalid_key)
                if invalid_raw and tonumber(invalid_raw) > fetch_start then
                    return 0
                end
                
                redis.call('SETEX', cache_key, cache_ttl, token_val)
                return 1
                """
                invalid_key = f"oauth2_invalid:{credential_uuid}"
                success = await redis_client.eval(
                    lua_script, 2, invalid_key, cache_key, fetch_start, cache_ttl, token
                )
                if success == 1:
                    logger.debug(
                        f"OAuth2 token cached for {credential_uuid} "
                        f"(TTL={cache_ttl}s, provider expires_in={expires_in}s)"
                    )
                else:
                    logger.info(
                        f"Skipping cache write for {credential_uuid}: invalidated during in-flight fetch."
                    )
            except Exception as exc:
                logger.warning(
                    f"OAuth2 token cache write failed for {credential_uuid}: {exc}"
                )
        else:
            logger.debug(
                f"OAuth2 token for {credential_uuid} has expires_in={expires_in}s "
                f"(<= margin {_EXPIRY_MARGIN}s); skipping cache to avoid serving expired token."
            )

        return token


async def invalidate_token(credential_uuid: str) -> None:
    """Delete a cached token (force re-fetch on next call).

    Call when:
    - Credential is updated (secret rotated).
    - Credential is deleted.
    - A 401 is received from the downstream API.
    """
    cache_key = f"oauth2_token:{credential_uuid}"
    try:
        redis_client = _get_redis()
        # Mark invalidated with a TTL that exceeds any reasonable fetch timeout
        await redis_client.setex(f"oauth2_invalid:{credential_uuid}", 3600, time.time())
        await redis_client.delete(cache_key)
        logger.info(f"OAuth2 token cache invalidated for {credential_uuid}")
    except Exception as exc:
        logger.warning(
            f"OAuth2 token cache invalidation failed for {credential_uuid}: {exc}"
        )


# ---------------------------------------------------------------------------
# Backward-compat alias: old code imported OAuth2TokenCache class methods.
# Keep this so we don't break anything in a staged migration.
# ---------------------------------------------------------------------------
class OAuth2TokenCache:
    """Thin shim retained for backward compatibility. New code should import
    the module-level get_or_fetch_token() and invalidate_token() directly."""

    @classmethod
    async def get_valid_token(
        cls,
        credential_uuid: str,
        token_url: str,
        client_id: str,
        client_secret: str,
        scope: Optional[str] = None,
        audience: Optional[str] = None,
    ) -> str:
        return await get_or_fetch_token(
            credential_uuid=credential_uuid,
            client_id=client_id,
            client_secret=client_secret,
            token_url=token_url,
            scope=scope,
            audience=audience,
        )

    @classmethod
    async def invalidate_token(cls, credential_uuid: str) -> None:
        await invalidate_token(credential_uuid)


async def _fetch_token(
    client_id: str,
    client_secret: str,
    token_url: str,
    scope: Optional[str],
    audience: Optional[str],
) -> tuple[str, int]:
    """POST to token_url and return (access_token, expires_in).

    Raises:
        ValueError: on network error, non-200 HTTP, or malformed response.
    """
    # Guard against SSRF: reject private/loopback/internal addresses in SaaS.
    try:
        pinned_ip = validate_user_configured_service_url(
            token_url, field_name="token_url"
        )
    except ValueError as exc:
        raise ValueError(f"OAuth2 token_url is not permitted: {exc}") from exc

    # Reject non-HTTPS token URLs to prevent sending client secrets in cleartext.
    from urllib.parse import urlparse as _urlparse

    _parsed = _urlparse(token_url)
    if _parsed.scheme != "https":
        raise ValueError(
            "OAuth2 token_url must use HTTPS to protect client credentials."
        )

    data: dict = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if scope:
        data["scope"] = scope
    if audience:
        data["audience"] = audience

    try:
        transport = get_pinned_httpx_transport(pinned_ip)
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT, transport=transport
        ) as client:
            response = await client.post(
                token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.RequestError as exc:
        raise ValueError(f"OAuth2 token fetch failed (network error): {exc}") from exc

    if response.status_code != 200:
        # Do NOT include the response body in the error — it may contain
        # sensitive private-service response data.
        raise ValueError(f"OAuth2 token endpoint returned HTTP {response.status_code}.")

    try:
        body = response.json()
    except Exception as exc:
        raise ValueError(
            f"OAuth2 token endpoint returned non-JSON response: {exc}"
        ) from exc

    if not isinstance(body, dict):
        raise ValueError("OAuth2 token endpoint response must be a JSON object.")

    token_type = body.get("token_type", "")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise ValueError(
            f"Unsupported OAuth2 token_type '{token_type}'. Only 'Bearer' is supported."
        )

    token = body.get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError(
            "OAuth2 token response is missing a valid 'access_token' string."
        )

    expires_in = int(body.get("expires_in", 0))
    return token, expires_in
