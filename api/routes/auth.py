import urllib.parse
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from loguru import logger
from pydantic import BaseModel

from api.constants import ENABLE_SIGNUP, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI
from api.db import db_client
from api.db.models import UserModel
from api.enums import PostHogEvent
from api.schemas.auth import AuthResponse, LoginRequest, SignupRequest, UserResponse
from api.services.auth.depends import get_user, require_local_auth
from api.services.organization_bootstrap import ensure_organization_bootstrapped
from api.services.posthog_client import capture_event
from api.utils.auth import create_jwt_token, hash_password, verify_password

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
)  # updated: 2026-09-23 profile endpoints

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


@router.get("/google")
async def google_oauth_start(redirect_uri: Optional[str] = None):
    """Redirect user to Google's OAuth 2.0 consent screen."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Google OAuth is not configured")
    target_redirect_uri = redirect_uri or GOOGLE_REDIRECT_URI
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": target_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=url)


@router.get("/google/callback", response_model=AuthResponse)
async def google_oauth_callback(
    code: str,
    error: str | None = None,
    redirect_uri: Optional[str] = None,
):
    """Exchange Google OAuth code for a JWT token, creating user if needed."""
    if error:
        raise HTTPException(status_code=400, detail=f"Google OAuth error: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=501, detail="Google OAuth is not configured")

    target_redirect_uri = redirect_uri or GOOGLE_REDIRECT_URI

    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": target_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            logger.error(f"[Google OAuth] Token exchange failed: {token_resp.text}")
            raise HTTPException(status_code=400, detail="Failed to exchange Google code for token")

        token_data = token_resp.json()
        access_token = token_data.get("access_token")

        # Fetch user info from Google
        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if userinfo_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to fetch Google user info")

        userinfo = userinfo_resp.json()

    google_email = userinfo.get("email")
    google_name = userinfo.get("name") or ""
    google_sub = userinfo.get("sub")  # stable Google user ID

    if not google_email:
        raise HTTPException(status_code=400, detail="No email returned from Google")

    # Find or create user in our DB
    existing_user = await db_client.get_user_by_email(google_email)

    if existing_user:
        user = existing_user
    else:
        # Create new user (no password hash for OAuth users)
        user = await db_client.create_user_with_email(
            email=google_email,
            password_hash=None,
            name=google_name,
        )

        # Bootstrap organization
        org_provider_id = f"org_{user.provider_id}"
        organization, _ = await db_client.get_or_create_organization_by_provider_id(
            org_provider_id=org_provider_id, user_id=user.id
        )
        await db_client.add_user_to_organization(user.id, organization.id)
        await db_client.update_user_selected_organization(user.id, organization.id)
        await ensure_organization_bootstrapped(organization.id, created_by=user.provider_id)

        capture_event(
            distinct_id=str(user.provider_id),
            event=PostHogEvent.SIGNED_UP,
            properties={"organization_id": organization.id, "auth_provider": "google"},
        )

    token = create_jwt_token(user.id, user.email or google_email)

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.SIGNED_IN,
        properties={"organization_id": user.selected_organization_id, "auth_provider": "google"},
    )

    return AuthResponse(
        token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            name=google_name,
            organization_id=user.selected_organization_id,
            provider_id=user.provider_id,
        ),
    )




@router.post(
    "/signup",
    response_model=AuthResponse,
    dependencies=[Depends(require_local_auth)],
)
async def signup(request: SignupRequest):
    if not ENABLE_SIGNUP:
        raise HTTPException(status_code=403, detail="Signup is disabled")

    # Check if email is already taken
    existing_user = await db_client.get_user_by_email(request.email)
    if existing_user:
        raise HTTPException(status_code=409, detail="Email already registered")

    # Hash password and create user
    hashed = hash_password(request.password)
    user = await db_client.create_user_with_email(
        email=request.email,
        password_hash=hashed,
        name=request.name,
    )

    # Create organization for the user
    org_provider_id = f"org_{user.provider_id}"
    organization, _ = await db_client.get_or_create_organization_by_provider_id(
        org_provider_id=org_provider_id, user_id=user.id
    )

    # Link user to organization
    await db_client.add_user_to_organization(user.id, organization.id)
    await db_client.update_user_selected_organization(user.id, organization.id)

    # Create default service configuration. This never raises, so signup still
    # succeeds if MPS is down; `_handle_oss_auth` re-enters bootstrap on the
    # user's subsequent authenticated requests, so a failure here is recovered
    # rather than permanent. Doing it here anyway means the common case has a
    # model configuration and SIP connectivity by the time the UI first loads.
    await ensure_organization_bootstrapped(
        organization.id,
        created_by=user.provider_id,
    )

    # Create JWT token
    token = create_jwt_token(user.id, request.email)

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.SIGNED_UP,
        properties={
            "organization_id": organization.id,
            "auth_provider": "local",
        },
    )

    return AuthResponse(
        token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            name=request.name,
            organization_id=organization.id,
            provider_id=user.provider_id,
        ),
    )


@router.post(
    "/login",
    response_model=AuthResponse,
    dependencies=[Depends(require_local_auth)],
)
async def login(request: LoginRequest):
    logger.info(f"[AUTH] Login attempt for email: {request.email}")
    # Look up user by email
    user = await db_client.get_user_by_email(request.email)
    if not user or not user.password_hash:
        logger.warning(f"[AUTH] Login failed: User '{request.email}' not found or no password hash in database")
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Verify password
    if not verify_password(request.password, user.password_hash):
        logger.warning(f"[AUTH] Login failed: Incorrect password provided for '{request.email}'")
        raise HTTPException(status_code=401, detail="Invalid email or password")

    logger.info(f"[AUTH] Login successful for user_id={user.id} ({user.email})")
    # Create JWT token
    token = create_jwt_token(user.id, user.email)

    capture_event(
        distinct_id=str(user.provider_id),
        event=PostHogEvent.SIGNED_IN,
        properties={
            "organization_id": user.selected_organization_id,
            "auth_provider": "local",
        },
    )

    return AuthResponse(
        token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            organization_id=user.selected_organization_id,
            provider_id=user.provider_id,
        ),
    )


@router.get("/me")
async def get_current_user(user: UserModel = Depends(get_user)):
    # Fetch name and phone from user configuration store
    profile = await db_client.get_user_configuration_value(user.id, "USER_PROFILE") or {}
    return {
        "id": user.id,
        "email": user.email,
        "name": profile.get("name") if profile.get("name") else None,
        "phone": profile.get("phone") if profile.get("phone") else None,
        "organization_id": user.selected_organization_id,
        "provider_id": user.provider_id,
    }


class UserProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None


@router.patch("/me")
async def update_current_user(
    request: UserProfileUpdateRequest,
    user: UserModel = Depends(get_user),
):
    """Update the authenticated user's profile (name, phone)."""
    profile = await db_client.get_user_configuration_value(user.id, "USER_PROFILE") or {}
    if request.name is not None:
        profile["name"] = request.name.strip()
    if request.phone is not None:
        profile["phone"] = request.phone.strip()
    # Persist to DB
    saved = await db_client.upsert_user_configuration_value(user.id, "USER_PROFILE", profile)
    # Use the actually-saved value for the response
    saved_profile = saved if isinstance(saved, dict) else profile
    return {
        "id": user.id,
        "email": user.email,
        "name": saved_profile.get("name") if saved_profile.get("name") else None,
        "phone": saved_profile.get("phone") if saved_profile.get("phone") else None,
        "organization_id": user.selected_organization_id,
        "provider_id": user.provider_id,
    }


class ChangePasswordRequest(BaseModel):
    current_password: Optional[str] = ""
    new_password: str


@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    current_user: UserModel = Depends(get_user),
):
    """Change current authenticated user's password."""
    from sqlalchemy import update
    from pydantic import BaseModel

    if not request.new_password or len(request.new_password.strip()) < 6:
        raise HTTPException(
            status_code=400,
            detail="New password must be at least 6 characters long",
        )

    # Check current password if user has an existing password hash
    if current_user.password_hash:
        if not request.current_password:
            raise HTTPException(
                status_code=400,
                detail="Current password is required",
            )
        if not verify_password(request.current_password, current_user.password_hash):
            logger.warning(f"[AUTH] Change password failed: incorrect current password for user {current_user.email}")
            raise HTTPException(
                status_code=400,
                detail="Current password is incorrect",
            )

    new_hash = hash_password(request.new_password.strip())

    async with db_client.async_session() as session:
        stmt = (
            update(UserModel)
            .where(UserModel.id == current_user.id)
            .values(password_hash=new_hash)
        )
        await session.execute(stmt)
        await session.commit()

    logger.info(f"[AUTH] Password changed successfully for user {current_user.email}")
    return {"status": "success", "message": "Password changed successfully"}


