from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from api.constants import RATE_LIMIT_LOGIN_PER_MIN, RATE_LIMIT_SIGNUP_PER_MIN
from api.db import db_client
from api.db.models import UserModel
from api.enums import PostHogEvent
from api.schemas.auth import AuthResponse, LoginRequest, SignupRequest, UserResponse
from api.services.auth.admin_emails import promote_if_admin_email
from api.services.auth.depends import create_user_configuration_with_mps_key, get_user
from api.services.posthog_client import capture_event
from api.services.rate_limit import rate_limit_ip
from api.services.voicelink_clients import provision_voicelink_client_for_signup
from api.utils.auth import create_jwt_token, hash_password, verify_password

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
)


@router.post("/signup", response_model=AuthResponse)
async def signup(
    request: SignupRequest,
    _rl: None = Depends(rate_limit_ip("auth:signup", RATE_LIMIT_SIGNUP_PER_MIN)),
):
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

    # Promote to superuser if the email is configured in ADMIN_EMAILS
    user = await promote_if_admin_email(user)

    # Create organization for the user
    org_provider_id = f"org_{user.provider_id}"
    organization, _ = await db_client.get_or_create_organization_by_provider_id(
        org_provider_id=org_provider_id, user_id=user.id
    )

    # Link user to organization
    await db_client.add_user_to_organization(user.id, organization.id)
    await db_client.update_user_selected_organization(user.id, organization.id)

    # Create default service configuration
    try:
        mps_config = await create_user_configuration_with_mps_key(
            user.id, organization.id, user.provider_id
        )
        if mps_config:
            await db_client.update_user_configuration(user.id, mps_config)
    except Exception:
        logger.warning(
            "Failed to create default configuration for OSS user", exc_info=True
        )

    # Best-effort: create a VoiceLink client for the new org. Never fails
    # signup; skips ADMIN_EMAILS users and unset reseller credentials. The
    # plaintext password is forwarded to VoiceLink only — never logged.
    await provision_voicelink_client_for_signup(
        organization_id=organization.id,
        email=request.email,
        password=request.password,
        name=request.name,
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
            is_superuser=bool(user.is_superuser),
        ),
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    request: LoginRequest,
    _rl: None = Depends(rate_limit_ip("auth:login", RATE_LIMIT_LOGIN_PER_MIN)),
):
    # Look up user by email
    user = await db_client.get_user_by_email(request.email)
    if not user or not user.password_hash:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Verify password
    if not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Promote to superuser if the email is configured in ADMIN_EMAILS
    user = await promote_if_admin_email(user)

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
            is_superuser=bool(user.is_superuser),
        ),
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user(user: UserModel = Depends(get_user)):
    return UserResponse(
        id=user.id,
        email=user.email,
        organization_id=user.selected_organization_id,
        provider_id=user.provider_id,
        is_superuser=bool(user.is_superuser),
    )
