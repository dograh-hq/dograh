"""Redis-backed cache for OAuth2 client credential tokens.

Key:   oauth2_token:<credential_uuid>
Value: raw access_token string
TTL:   max(30, expires_in - 60) seconds

Spec §5.2 interface: module-level functions get_or_fetch_token() and invalidate_token().
"""

from typing import Optional

import httpx
import redis.asyncio as aioredis
from loguru import logger

from api.constants import REDIS_URL

_MIN_TTL = 30          # Floor: never cache for less than 30s
_EXPIRY_MARGIN = 60    # Pre-expire: refresh 60s before real expiry
_FETCH_TIMEOUT = 10.0  # Token endpoint timeout in seconds


async def get_or_fetch_token(
    credential_uuid: str,
    client_id: str,
    client_secret: str,
    token_url: str,
    scope: Optional[str] = None,
    audience: Optional[str] = None,
) -> str:
    """Return a valid access token, using Redis cache when possible.

    Raises:
        ValueError: If the token fetch fails for any reason.
    """
    cache_key = f"oauth2_token:{credential_uuid}"
    redis_client = None

    try:
        redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
        cached = await redis_client.get(cache_key)
        if cached:
            logger.debug(f"Using cached OAuth2 token for credential {credential_uuid}")
            return cached
    except Exception as exc:
        # Redis unavailable: skip cache, fetch fresh. Never crash the call.
        logger.warning(
            f"OAuth2 token cache read failed for {credential_uuid}: {exc}"
        )
        redis_client = None

    # Cache miss (or Redis down) — fetch a fresh token.
    logger.info(f"Fetching new OAuth2 token for credential {credential_uuid}")
    token, expires_in = await _fetch_token(
        client_id=client_id,
        client_secret=client_secret,
        token_url=token_url,
        scope=scope,
        audience=audience,
    )

    # Write to cache if Redis is available.
    if redis_client is not None:
        ttl = max(_MIN_TTL, expires_in - _EXPIRY_MARGIN)
        try:
            await redis_client.setex(cache_key, ttl, token)
            logger.debug(
                f"OAuth2 token cached for {credential_uuid} "
                f"(TTL={ttl}s, provider expires_in={expires_in}s)"
            )
        except Exception as exc:
            logger.warning(
                f"OAuth2 token cache write failed for {credential_uuid}: {exc}"
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
        redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
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
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
            response = await client.post(
                token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.RequestError as exc:
        raise ValueError(
            f"OAuth2 token fetch failed (network error): {exc}"
        ) from exc

    if response.status_code != 200:
        # Truncate — never log the full response body (may contain sensitive info)
        preview = response.text[:200]
        raise ValueError(
            f"OAuth2 token endpoint returned HTTP {response.status_code}. "
            f"Response preview: {preview}"
        )

    try:
        body = response.json()
    except Exception as exc:
        raise ValueError(
            f"OAuth2 token endpoint returned non-JSON response: {exc}"
        ) from exc

    token = body.get("access_token")
    if not token:
        raise ValueError(
            "OAuth2 token endpoint response missing 'access_token' field."
        )

    expires_in = int(body.get("expires_in", 3600))
    return token, expires_in
