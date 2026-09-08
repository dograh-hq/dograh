"""Fail-closed isolated administrative authentication boundary."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request, status

from .config import Settings


@dataclass(frozen=True)
class AdminPrincipal:
    """A deliberately non-sensitive audit identity.

    The token itself is never logged. A deployment can put SSO/VPN in front of
    this service; the Explorer still requires this service-side credential.
    """

    subject: str = "configured-admin"


def require_admin(settings: Settings):
    async def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> AdminPrincipal:
        expected = f"Bearer {settings.admin_token}"
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Administrator authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return AdminPrincipal()

    return dependency
