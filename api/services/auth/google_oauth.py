"""Google OAuth2 (authorization-code) helpers for 'Sign in with Google'.

Build the consent URL, exchange the code for tokens, fetch the verified profile.
Client id is public; the secret is server-side only. Activates when GOOGLE_CLIENT_ID
+ GOOGLE_CLIENT_SECRET are set in env.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlencode

import httpx
from loguru import logger

from api.constants import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def is_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def build_consent_url(state: str) -> str:
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_userinfo(code: str) -> Optional[dict]:
    """Exchange the auth code for tokens, then fetch the user's profile.

    Returns the userinfo dict ({email, email_verified, name, sub, ...}) or None.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            tok = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": GOOGLE_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
            )
            if not tok.is_success:
                logger.warning(
                    f"Google token exchange failed: {tok.status_code} {tok.text[:200]}"
                )
                return None
            access_token = tok.json().get("access_token")
            if not access_token:
                return None
            info = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if not info.is_success:
                logger.warning(f"Google userinfo failed: {info.status_code}")
                return None
            return info.json()
    except Exception as exc:
        logger.warning(f"Google OAuth error: {exc}")
        return None
