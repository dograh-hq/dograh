"""'Sign in with Google' — OAuth2 authorization-code flow.

/auth/google/login -> Google consent (with a CSRF state cookie) -> Google redirects
to /auth/google/callback -> we verify state, exchange the code, find-or-create the
user (same org setup as signup), issue our JWT, and hand off to a UI callback page
that sets the session cookie.
"""

import base64
import json
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from loguru import logger

from api.constants import UI_APP_URL
from api.db import db_client
from api.services.auth import google_oauth
from api.services.auth.admin_emails import promote_if_admin_email
from api.services.auth.depends import create_user_configuration_with_mps_key
from api.services.voicelink_clients import provision_voicelink_client_for_signup
from api.utils.auth import create_jwt_token

router = APIRouter(prefix="/auth", tags=["auth"])

_STATE_COOKIE = "g_oauth_state"
_STATE_PATH = "/api/v1/auth/google"


def _fail(reason: str) -> RedirectResponse:
    return RedirectResponse(f"{UI_APP_URL}/auth/login?error={reason}")


@router.get("/google/login")
async def google_login():
    """Redirect to Google's consent screen (sets a short-lived CSRF state cookie)."""
    if not google_oauth.is_configured():
        raise HTTPException(status_code=503, detail="google_auth_not_configured")
    state = secrets.token_urlsafe(24)
    resp = RedirectResponse(google_oauth.build_consent_url(state))
    resp.set_cookie(
        _STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        secure=True,
        samesite="lax",
        path=_STATE_PATH,
    )
    return resp


@router.get("/google/callback")
async def google_callback(
    request: Request, code: str = "", state: str = "", error: str = ""
):
    if error or not code:
        return _fail("google")

    # CSRF: the state we set must come back unchanged.
    if not state or request.cookies.get(_STATE_COOKIE) != state:
        return _fail("google_state")

    info = await google_oauth.exchange_code_for_userinfo(code)
    email = (info or {}).get("email")
    if not email or (info or {}).get("email_verified") is False:
        return _fail("google_email")

    name = info.get("name") or email.split("@")[0]
    user = await _find_or_create_google_user(email, name)

    token = create_jwt_token(user.id, email)
    user_json = {
        "id": user.id,
        "email": email,
        "name": name,
        "provider": "google",
        "is_superuser": bool(user.is_superuser),
    }
    payload = base64.urlsafe_b64encode(json.dumps(user_json).encode()).decode()
    # Token in the URL FRAGMENT so it never reaches a server log / Referer.
    resp = RedirectResponse(f"{UI_APP_URL}/auth/google/callback#token={token}&user={payload}")
    resp.delete_cookie(_STATE_COOKIE, path=_STATE_PATH)
    return resp


async def _find_or_create_google_user(email: str, name: str):
    """Return the user for this Google email, creating + setting up the org if new."""
    user = await db_client.get_user_by_email(email)
    if user:
        return await promote_if_admin_email(user)

    # New user — mirror the signup org setup (no password; Google is the credential).
    user = await db_client.create_user_with_email(
        email=email, password_hash=None, name=name
    )
    user = await promote_if_admin_email(user)

    org_provider_id = f"org_{user.provider_id}"
    organization, _ = await db_client.get_or_create_organization_by_provider_id(
        org_provider_id=org_provider_id, user_id=user.id
    )
    await db_client.add_user_to_organization(user.id, organization.id)
    await db_client.update_user_selected_organization(user.id, organization.id)

    try:
        mps_config = await create_user_configuration_with_mps_key(
            user.id, organization.id, user.provider_id
        )
        if mps_config:
            await db_client.update_user_configuration(user.id, mps_config)
    except Exception:
        logger.warning("Failed to create default config for Google user", exc_info=True)

    # Best-effort VoiceLink client (needs a password — generate one; user logs in
    # to OUR app via Google, never needs this).
    try:
        await provision_voicelink_client_for_signup(
            organization_id=organization.id,
            email=email,
            password=secrets.token_urlsafe(16),
            name=name,
        )
    except Exception:
        logger.warning("VoiceLink provision skipped for Google user", exc_info=True)

    return user
