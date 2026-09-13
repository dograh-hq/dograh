"""TURN credentials endpoint for time-limited WebRTC authentication.

This module implements the TURN REST API credential generation as specified in
draft-uberti-behave-turn-rest-00. It generates ephemeral credentials that are
valid for a configurable TTL and are cryptographically bound to the user.

The credential format:
- Username: {expiration_timestamp}:{user_id}
- Password: base64(hmac-sha1(shared_secret, username))

References:
- https://datatracker.ietf.org/doc/html/draft-uberti-behave-turn-rest-00
- https://github.com/coturn/coturn/wiki/turnserver#turn-rest-api
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel

from api.constants import ENABLE_COTURN, TURN_SECRET
from api.db.models import UserModel
from api.services.auth.depends import get_user
from api.services.turn import generate_turn_credentials

router = APIRouter(prefix="/turn", tags=["turn"])


class TurnCredentialsResponse(BaseModel):
    """Response model for TURN credentials."""

    username: str
    password: str
    ttl: int
    uris: List[str]


class TurnConfigResponse(BaseModel):
    """Response model for TURN configuration status."""

    enabled: bool
    host: Optional[str] = None


@router.get("/credentials", response_model=TurnCredentialsResponse)
async def get_turn_credentials(
    user: UserModel = Depends(get_user),
) -> TurnCredentialsResponse:
    """Get time-limited TURN credentials for WebRTC connections.

    This endpoint generates ephemeral TURN credentials that are:
    - Valid for the configured TTL (default: 24 hours)
    - Cryptographically bound to the user via HMAC
    - Compatible with coturn's use-auth-secret mode

    Returns:
        TurnCredentialsResponse with username, password, ttl, and TURN URIs
    """
    if not ENABLE_COTURN:
        logger.warning("TURN credentials requested but ENABLE_COTURN is false")
        raise HTTPException(
            status_code=503,
            detail="TURN server not configured",
        )

    if not TURN_SECRET:
        logger.warning("TURN credentials requested but TURN_SECRET not configured")
        raise HTTPException(
            status_code=503,
            detail="TURN server not configured",
        )

    try:
        credentials = generate_turn_credentials(str(user.id))
        logger.debug(f"Generated TURN credentials for user {user.id}")
        return TurnCredentialsResponse(**credentials)
    except Exception as e:
        logger.error(f"Failed to generate TURN credentials: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to generate TURN credentials",
        )
