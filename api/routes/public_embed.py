"""Public API endpoints for workflow embedding.

These endpoints are accessible without authentication but require valid embed tokens.
They handle CORS, domain validation, and session management for embedded workflows.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Optional

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    Response,
)
from loguru import logger
from pydantic import BaseModel

from api.db import db_client
from api.enums import WorkflowRunMode

router = APIRouter(prefix="/public/embed")


class InitEmbedRequest(BaseModel):
    """Request model for initializing an embed session"""

    token: str
    context_variables: Optional[dict] = None


class InitEmbedResponse(BaseModel):
    """Response model for embed initialization"""

    session_token: str
    workflow_run_id: int
    config: dict


class EmbedConfigResponse(BaseModel):
    """Response model for embed configuration"""

    workflow_id: int
    settings: dict
    theme: str
    position: str
    button_text: str
    button_color: str
    size: str
    auto_start: bool


def validate_origin(origin: str, allowed_domains: list) -> bool:
    """Validate if the origin is in the allowed domains list.

    Args:
        origin: The origin header from the request
        allowed_domains: List of allowed domain patterns

    Returns:
        True if origin is allowed, False otherwise
    """
    if not allowed_domains:
        # If no domains specified, allow all origins
        return True

    # Extract domain from origin (remove protocol)
    if "://" in origin:
        domain = origin.split("://")[1].split("/")[0].split(":")[0]
    else:
        domain = origin

    # Normalize domain for www matching
    def normalize_www(d: str) -> tuple[str, str]:
        """Return both www and non-www versions of a domain"""
        if d.startswith("www."):
            return (d, d[4:])  # (www.x.com, x.com)
        else:
            return (d, f"www.{d}")  # (x.com, www.x.com)

    domain_variants = normalize_www(domain)

    for allowed in allowed_domains:
        if allowed == "*":
            return True
        elif allowed.startswith("*."):
            # Wildcard subdomain matching
            base_domain = allowed[2:]
            if domain == base_domain or domain.endswith("." + base_domain):
                return True
        else:
            # Check both www and non-www versions
            allowed_variants = normalize_www(allowed)
            # If any variant of domain matches any variant of allowed, it's valid
            if any(
                dv in allowed_variants or av in domain_variants
                for dv in domain_variants
                for av in allowed_variants
            ):
                return True

    return False


def generate_session_token() -> str:
    """Generate a cryptographically secure session token"""
    return f"emb_session_{secrets.token_urlsafe(32)}"


@router.post("/init", response_model=InitEmbedResponse)
async def initialize_embed_session(request: Request, init_request: InitEmbedRequest):
    """Initialize an embed session with token validation and domain checking.

    This endpoint:
    1. Validates the embed token
    2. Checks domain whitelist
    3. Creates a workflow run
    4. Generates a temporary session token
    5. Returns configuration for the widget
    """
    # Get origin header for domain validation
    origin = request.headers.get("origin", "")
    if not origin:
        origin = request.headers.get("referer", "")

    # Validate embed token
    embed_token = await db_client.get_embed_token_by_token(init_request.token)
    if not embed_token:
        raise HTTPException(status_code=404, detail="Invalid embed token")

    # Check if token is active
    if not embed_token.is_active:
        raise HTTPException(status_code=403, detail="Embed token is inactive")

    # Check expiration
    if embed_token.expires_at and embed_token.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=403, detail="Embed token has expired")

    # Check usage limit
    if embed_token.usage_limit and embed_token.usage_count >= embed_token.usage_limit:
        raise HTTPException(status_code=403, detail="Embed token usage limit exceeded")

    # Validate domain
    if not validate_origin(origin, embed_token.allowed_domains or []):
        logger.warning(
            f"Domain validation failed: {origin} not in {embed_token.allowed_domains}"
        )
        raise HTTPException(status_code=403, detail=f"Domain not allowed: {origin}")

    # Create workflow run
    try:
        workflow_run = await db_client.create_workflow_run(
            name=f"Embed Run - {datetime.now(UTC).isoformat()}",
            workflow_id=embed_token.workflow_id,
            mode=WorkflowRunMode.SMALLWEBRTC.value,
            user_id=embed_token.created_by,  # Use token creator as run owner
            initial_context=init_request.context_variables,
        )
    except Exception as e:
        logger.error(f"Failed to create workflow run: {e}")
        raise HTTPException(status_code=500, detail="Failed to create workflow run")

    # Generate session token
    session_token = generate_session_token()

    # Create embed session
    try:
        await db_client.create_embed_session(
            session_token=session_token,
            embed_token_id=embed_token.id,
            workflow_run_id=workflow_run.id,
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
            origin=origin[:255],
            expires_at=datetime.now(UTC) + timedelta(hours=1),  # 1 hour expiry
        )
    except Exception as e:
        logger.error(f"Failed to create embed session: {e}")
        raise HTTPException(status_code=500, detail="Failed to create session")

    # Increment usage count
    await db_client.increment_embed_token_usage(embed_token.id)

    # Prepare configuration
    config = {
        "workflow_id": embed_token.workflow_id,
        "workflow_run_id": workflow_run.id,
        **(embed_token.settings or {}),
    }

    return InitEmbedResponse(
        session_token=session_token, workflow_run_id=workflow_run.id, config=config
    )


@router.get("/config/{token}", response_model=EmbedConfigResponse)
async def get_embed_config(token: str, request: Request):
    """Get embed configuration without creating a session.

    This endpoint is used to fetch widget configuration for display purposes
    without actually starting a call session.
    """
    # Get origin header for domain validation
    origin = request.headers.get("origin", "")
    if not origin:
        origin = request.headers.get("referer", "")

    # Validate embed token
    embed_token = await db_client.get_embed_token_by_token(token)
    if not embed_token:
        raise HTTPException(status_code=404, detail="Invalid embed token")

    # Check if token is active
    if not embed_token.is_active:
        raise HTTPException(status_code=403, detail="Embed token is inactive")

    # Validate domain
    if not validate_origin(origin, embed_token.allowed_domains or []):
        raise HTTPException(status_code=403, detail=f"Domain not allowed: {origin}")

    # Extract settings with defaults
    settings = embed_token.settings or {}

    return EmbedConfigResponse(
        workflow_id=embed_token.workflow_id,
        settings=settings,
        theme=settings.get("theme", "light"),
        position=settings.get("position", "bottom-right"),
        button_text=settings.get("buttonText", "Start Voice Call"),
        button_color=settings.get("buttonColor", "#3B82F6"),
        size=settings.get("size", "medium"),
        auto_start=settings.get("autoStart", False),
    )


@router.options("/init")
async def options_init(request: Request):
    """Handle CORS preflight for init endpoint"""
    # For init endpoint, we need to check the token in the request body
    # But OPTIONS requests don't have body, so we'll be permissive
    # The actual validation happens in the POST request
    origin = request.headers.get("origin", "*")

    return Response(
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Origin",
            "Access-Control-Max-Age": "86400",
        }
    )


@router.options("/config/{token}")
async def options_config(request: Request, token: str):
    """Handle CORS preflight for config endpoint"""
    # Get origin header
    origin = request.headers.get("origin", "*")

    # Try to validate the token and get allowed domains
    allowed_origin = origin
    try:
        embed_token = await db_client.get_embed_token_by_token(token)
        if embed_token and embed_token.is_active:
            # Check if origin is in allowed domains
            if validate_origin(origin, embed_token.allowed_domains or []):
                allowed_origin = origin
            else:
                # If not allowed, don't include the origin
                allowed_origin = ""
    except Exception:
        # On error, be permissive for OPTIONS
        pass

    return Response(
        headers={
            "Access-Control-Allow-Origin": allowed_origin,
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "86400",
        }
    )
