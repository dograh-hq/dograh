"""TURN credential minting.

Time-limited coturn credentials, per the TURN REST API
(draft-uberti-behave-turn-rest-00): the username carries the expiry and the
password is an HMAC of it under the shared secret.

Lives in ``services`` rather than in the route that exposes it because the
callers are not all HTTP: the WhatsApp provider mints these on the worker that
builds a call's ICE servers, and importing an HTTP route module from a provider
transport pulls the web layer - and its router, auth dependencies and request
models - into background workers, for one pure function.

References:
- https://datatracker.ietf.org/doc/html/draft-uberti-behave-turn-rest-00
- https://github.com/coturn/coturn/wiki/turnserver#turn-rest-api
"""

import base64
import hashlib
import hmac
import time

from api.constants import (
    ENVIRONMENT,
    TURN_CREDENTIAL_TTL,
    TURN_HOST,
    TURN_PORT,
    TURN_SECRET,
    TURN_TLS_PORT,
)
from api.enums import Environment


def generate_turn_credentials(user_id: str, ttl: int = TURN_CREDENTIAL_TTL) -> dict:
    """Generate time-limited TURN credentials using HMAC-SHA1.

    Args:
        user_id: Unique identifier for the user (for auditing)
        ttl: Time-to-live in seconds for the credentials

    Returns:
        Dictionary with username, password, ttl, and TURN URIs

    Raises:
        ValueError: If TURN_SECRET is not configured
    """
    if not TURN_SECRET:
        raise ValueError("TURN_SECRET is not configured")

    # Calculate expiration timestamp
    expiration = int(time.time()) + ttl

    # Username format: {expiration}:{user_id}
    # This allows the TURN server to:
    # 1. Verify the credential hasn't expired
    # 2. Track usage per user for auditing
    username = f"{expiration}:{user_id}"

    # Password: base64(hmac-sha1(secret, username))
    # This is the standard TURN REST API algorithm
    password = base64.b64encode(
        hmac.new(
            TURN_SECRET.encode("utf-8"),
            username.encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("utf-8")

    # Build TURN URIs
    # Note: aiortc only uses the FIRST valid TURN URI, so ordering matters.
    # Priority:
    #   1. TURNS (TLS) if configured - most secure
    #   2. TURN TCP for LOCAL env (macOS Docker compatibility)
    #   3. TURN UDP for production (more efficient)
    uris = []

    # Add non-TLS TURN as fallback, ordered by environment
    if ENVIRONMENT == Environment.LOCAL.value:
        uris.extend(
            [
                f"turn:{TURN_HOST}:{TURN_PORT}?transport=tcp",  # TCP for macOS Docker
                f"turn:{TURN_HOST}:{TURN_PORT}",  # UDP fallback
            ]
        )
    else:
        uris.extend(
            [
                f"turn:{TURN_HOST}:{TURN_PORT}",  # UDP preferred for other environments
                f"turn:{TURN_HOST}:{TURN_PORT}?transport=tcp",  # TCP fallback
            ]
        )

    # Add TLS URIs if TLS port is configured
    if TURN_TLS_PORT:
        uris.extend(
            [
                f"turns:{TURN_HOST}:{TURN_TLS_PORT}",  # TURN over TLS
                f"turns:{TURN_HOST}:{TURN_TLS_PORT}?transport=tcp",  # TURN over TLS+TCP
            ]
        )

    return {
        "username": username,
        "password": password,
        "ttl": ttl,
        "uris": uris,
    }
