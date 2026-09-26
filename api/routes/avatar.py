"""SpatialReal avatar session endpoint.

Mints a short-lived AvatarKit session token so the browser can drive the
SpatialReal avatar in SDK mode. The SpatialReal API key never leaves the
backend; the client only receives the public app id, the avatar id, and the
session token (max 24h validity, capped by SpatialReal).

Token exchange (per the avatarkit server SDK):
POST {console_endpoint}/session-tokens with header X-Api-Key and body
{"expireAt": <unix seconds>} -> {"sessionToken": "..."}.
"""

import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

from api.constants import (
    SPATIALREAL_API_KEY,
    SPATIALREAL_APP_ID,
    SPATIALREAL_AVATAR_ID,
    SPATIALREAL_CONSOLE_ENDPOINT,
    SPATIALREAL_TOKEN_TTL,
)
from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.avatar import resolve_avatar_settings

router = APIRouter(prefix="/avatar", tags=["avatar"])

SESSION_TOKEN_PATH = "/session-tokens"


class AvatarSessionResponse(BaseModel):
    """Client-side configuration for an AvatarKit SDK-mode session."""

    app_id: str
    avatar_id: str
    session_token: str
    expires_at: int


class AvatarConfigResponse(BaseModel):
    """Whether the avatar feature is configured, and in which driving mode."""

    enabled: bool
    mode: str  # "sdk" | "host" | "off"


def _is_configured() -> bool:
    return bool(SPATIALREAL_APP_ID and SPATIALREAL_API_KEY and SPATIALREAL_AVATAR_ID)


async def _resolve_settings_for_run(
    workflow_run_id: int | None, user: UserModel
) -> dict:
    """Effective avatar settings — the run's pinned workflow config over env.

    Uses the same resolution as the pipeline (run definition's
    workflow_configurations), so browser and backend always agree.
    """
    run_configs = None
    if workflow_run_id is not None:
        workflow_run = await db_client.get_workflow_run(
            workflow_run_id, organization_id=user.selected_organization_id
        )
        if not workflow_run:
            raise HTTPException(status_code=404, detail="Workflow run not found")
        if workflow_run.definition is not None:
            run_configs = workflow_run.definition.workflow_configurations
    return resolve_avatar_settings(run_configs)


@router.get("/config", response_model=AvatarConfigResponse)
async def get_avatar_config(
    workflow_run_id: int | None = Query(default=None),
    user: UserModel = Depends(get_user),
) -> AvatarConfigResponse:
    settings = await _resolve_settings_for_run(workflow_run_id, user)
    return AvatarConfigResponse(enabled=settings["enabled"], mode=settings["mode"])


@router.post("/session", response_model=AvatarSessionResponse)
async def create_avatar_session(
    workflow_run_id: int | None = Query(default=None),
    user: UserModel = Depends(get_user),
) -> AvatarSessionResponse:
    """Mint a SpatialReal session token for the current user."""
    if not _is_configured():
        raise HTTPException(status_code=503, detail="Avatar engine not configured")

    settings = await _resolve_settings_for_run(workflow_run_id, user)
    if not settings["enabled"]:
        raise HTTPException(
            status_code=503, detail="Avatar disabled for this workflow"
        )

    expires_at = int(time.time()) + SPATIALREAL_TOKEN_TTL
    session_token = await mint_spatialreal_token(expires_at)

    logger.debug(f"Minted SpatialReal session token for user {user.id}")
    return AvatarSessionResponse(
        app_id=SPATIALREAL_APP_ID,
        avatar_id=settings["avatar_id"] or SPATIALREAL_AVATAR_ID,
        session_token=session_token,
        expires_at=expires_at,
    )


async def mint_spatialreal_token(expires_at: int) -> str:
    """Exchange the API key for a SpatialReal session token.

    Raises HTTPException (502) on any exchange failure; shared by the
    authenticated route above and the public embed avatar endpoints.
    """
    endpoint = SPATIALREAL_CONSOLE_ENDPOINT.rstrip("/") + SESSION_TOKEN_PATH

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                endpoint,
                json={"expireAt": expires_at},
                headers={
                    "X-Api-Key": SPATIALREAL_API_KEY,
                    "Content-Type": "application/json",
                },
            )
    except httpx.HTTPError as e:
        logger.error(f"SpatialReal session token request failed: {e}")
        raise HTTPException(
            status_code=502, detail="Failed to reach avatar engine"
        ) from e

    if response.status_code != 200:
        logger.error(
            f"SpatialReal session token request returned {response.status_code}: "
            f"{response.text[:500]}"
        )
        raise HTTPException(
            status_code=502, detail="Avatar engine rejected token request"
        )

    data = response.json()
    session_token = data.get("sessionToken")
    if not session_token or data.get("errors"):
        logger.error(f"SpatialReal session token response invalid: {str(data)[:500]}")
        raise HTTPException(
            status_code=502, detail="Avatar engine returned invalid token response"
        )

    return session_token
