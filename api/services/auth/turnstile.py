"""Cloudflare Turnstile server-side verification for auth endpoints.

Gated by TURNSTILE_SECRET_KEY:
  - unset           → not enforced (verify returns True) so dev / other
                      deployments aren't blocked
  - set + bad/missing token → rejected
  - set + Cloudflare unreachable → fail-OPEN (logged) so a CF outage can't
    lock everyone out; rate-limiting remains the backstop.

The secret key is read from the environment only and never logged.
"""

import os
from typing import Optional

import httpx
from loguru import logger

TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY", "")
_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def turnstile_enabled() -> bool:
    """True when a secret key is configured (i.e. verification is enforced)."""
    return bool(TURNSTILE_SECRET_KEY)


async def verify_turnstile(token: Optional[str], remoteip: Optional[str] = None) -> bool:
    """Verify a Turnstile token with Cloudflare. See module docstring for the
    fail-open / fail-closed policy."""
    if not TURNSTILE_SECRET_KEY:
        return True  # not enforced on this deployment

    if not token:
        return False  # enforced but the client sent nothing

    data = {"secret": TURNSTILE_SECRET_KEY, "response": token}
    if remoteip:
        data["remoteip"] = remoteip

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(_VERIFY_URL, data=data)
            result = resp.json()
    except Exception:
        # CF unreachable / timeout — don't lock users out; rate-limit still applies.
        logger.warning("Turnstile verification unreachable; allowing", exc_info=True)
        return True

    success = bool(result.get("success"))
    if not success:
        logger.warning(
            f"Turnstile verification failed: {result.get('error-codes')}"
        )
    return success
