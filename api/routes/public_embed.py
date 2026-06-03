"""Public API endpoints for workflow embedding.

These endpoints are accessible without authentication but require valid embed tokens.
They handle CORS, domain validation, and session management for embedded workflows.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Optional
from urllib.parse import urlsplit

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    Response,
)
from loguru import logger
from pydantic import BaseModel
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from api.db import db_client
from api.enums import WorkflowRunMode
from api.routes.turn_credentials import (
    TURN_SECRET,
    TurnCredentialsResponse,
    generate_turn_credentials,
)

router = APIRouter(prefix="/public/embed")

EMBED_CORS_ALLOW_HEADERS = "Content-Type, Origin"
EMBED_CORS_MAX_AGE = "86400"


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

    domain, origin_port = _parse_origin_host_port(origin)
    if not domain:
        return False

    # Normalize domain for www matching
    def normalize_www(d: str) -> tuple[str, str]:
        """Return both www and non-www versions of a domain"""
        if d.startswith("www."):
            return (d, d[4:])  # (www.x.com, x.com)
        else:
            return (d, f"www.{d}")  # (x.com, www.x.com)

    domain_variants = normalize_www(domain)

    for allowed in allowed_domains:
        allowed = str(allowed).strip().lower()
        if allowed == "*":
            return True
        allowed_domain, allowed_port = _parse_origin_host_port(allowed)
        if not allowed_domain:
            continue
        if allowed_port is not None and allowed_port != origin_port:
            continue

        if allowed_domain.startswith("*."):
            # Wildcard subdomain matching
            base_domain = allowed_domain[2:]
            if domain == base_domain or domain.endswith("." + base_domain):
                return True
        else:
            # Check both www and non-www versions
            allowed_variants = normalize_www(allowed_domain)
            # If any variant of domain matches any variant of allowed, it's valid
            if any(
                dv in allowed_variants or av in domain_variants
                for dv in domain_variants
                for av in allowed_variants
            ):
                return True

    return False


def _parse_origin_host_port(value: str) -> tuple[str, str | None]:
    candidate = value.strip().lower()
    if not candidate:
        return "", None

    if "://" not in candidate and not candidate.startswith("//"):
        candidate = f"//{candidate}"

    parsed = urlsplit(candidate)
    try:
        parsed_port = parsed.port
    except ValueError:
        parsed_port = None

    port = str(parsed_port) if parsed_port is not None else None
    return (parsed.hostname or "").rstrip("."), port


def generate_session_token() -> str:
    """Generate a cryptographically secure session token"""
    return f"emb_session_{secrets.token_urlsafe(32)}"


def get_request_origin(request: Request) -> str:
    """Extract origin from request headers, falling back to referer if not present."""
    origin = request.headers.get("origin", "")
    if not origin:
        origin = request.headers.get("referer", "")
    return origin


def _cors_response(origin: str, methods: str) -> Response:
    return Response(
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": methods,
            "Access-Control-Allow-Headers": EMBED_CORS_ALLOW_HEADERS,
            "Access-Control-Max-Age": EMBED_CORS_MAX_AGE,
            "Vary": "Origin",
        }
    )


def _allow_embed_origin(response: Response, origin: str) -> None:
    response.headers["Access-Control-Allow-Origin"] = origin
    vary = response.headers.get("Vary")
    if not vary:
        response.headers["Vary"] = "Origin"
        return

    vary_values = {value.strip().lower() for value in vary.split(",")}
    if "origin" not in vary_values:
        response.headers["Vary"] = f"{vary}, Origin"


async def _config_preflight_response(token: str, origin: str) -> Response:
    embed_token = await db_client.get_embed_token_by_token(token)
    if not embed_token or not embed_token.is_active:
        return Response(status_code=403)

    if not validate_origin(origin, embed_token.allowed_domains or []):
        return Response(status_code=403)

    return _cors_response(origin, "GET, OPTIONS")


async def _turn_credentials_preflight_response(
    session_token: str, origin: str
) -> Response:
    embed_session = await db_client.get_embed_session_by_token(session_token)
    if not embed_session:
        return Response(status_code=403)

    if embed_session.expires_at and embed_session.expires_at < datetime.now(UTC):
        return Response(status_code=403)

    embed_token = await db_client.get_embed_token_by_id(embed_session.embed_token_id)
    if not embed_token:
        return Response(status_code=403)

    if not validate_origin(origin, embed_token.allowed_domains or []):
        return Response(status_code=403)

    return _cors_response(origin, "GET, OPTIONS")


async def build_public_embed_preflight_response(
    path: str, origin: str, requested_method: str, api_prefix: str = "/api/v1"
) -> Response | None:
    """Handle embed preflights before global CORSMiddleware rejects external sites."""
    public_embed_prefix = f"{api_prefix.rstrip('/')}/public/embed"

    if path == f"{public_embed_prefix}/init":
        if requested_method.upper() != "POST":
            return Response(status_code=405)
        return _cors_response(origin, "POST, OPTIONS")

    config_prefix = f"{public_embed_prefix}/config/"
    if path.startswith(config_prefix):
        if requested_method.upper() != "GET":
            return Response(status_code=405)
        token = path[len(config_prefix) :].split("/", 1)[0]
        return await _config_preflight_response(token, origin)

    turn_credentials_prefix = f"{public_embed_prefix}/turn-credentials/"
    if path.startswith(turn_credentials_prefix):
        if requested_method.upper() != "GET":
            return Response(status_code=405)
        session_token = path[len(turn_credentials_prefix) :].split("/", 1)[0]
        return await _turn_credentials_preflight_response(session_token, origin)

    return None


class PublicEmbedCORSMiddleware:
    """Allow token-gated embed CORS before global SaaS CORS rejects preflights."""

    def __init__(self, app: ASGIApp, api_prefix: str = "/api/v1"):
        self.app = app
        self.api_prefix = api_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "OPTIONS":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        origin = headers.get("origin")
        requested_method = headers.get("access-control-request-method")

        if origin and requested_method:
            response = await build_public_embed_preflight_response(
                scope.get("path", ""), origin, requested_method, self.api_prefix
            )
            if response is not None:
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


@router.post("/init", response_model=InitEmbedResponse)
async def initialize_embed_session(
    request: Request, init_request: InitEmbedRequest, response: Response
):
    """Initialize an embed session with token validation and domain checking.

    This endpoint:
    1. Validates the embed token
    2. Checks domain whitelist
    3. Creates a workflow run
    4. Generates a temporary session token
    5. Returns configuration for the widget
    """
    origin = get_request_origin(request)

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

    if origin:
        _allow_embed_origin(response, origin)

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


@router.options("/config/{token}")
async def options_embed_config(token: str, request: Request):
    """Fallback OPTIONS handler for the embed config endpoint.

    Browser preflights include Access-Control-Request-Method and are handled by
    PublicEmbedCORSMiddleware before global CORS. This keeps non-conformant
    OPTIONS requests on the same validation path.
    """
    return await _config_preflight_response(token, request.headers.get("origin", ""))


@router.get("/config/{token}", response_model=EmbedConfigResponse)
async def get_embed_config(token: str, request: Request, response: Response):
    """Get embed configuration without creating a session.

    This endpoint is used to fetch widget configuration for display purposes
    without actually starting a call session.
    """
    origin = get_request_origin(request)

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

    # Set CORS header explicitly; the global CORSMiddleware covers only
    # first-party origins; this endpoint is fetched by external embed sites.
    if origin:
        _allow_embed_origin(response, origin)

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
    """Fallback OPTIONS handler for init endpoint."""
    # Browser preflights are handled by PublicEmbedCORSMiddleware before global CORS.
    # For init endpoint, we need to check the token in the request body
    # But OPTIONS requests don't have body, so we'll be permissive
    # The actual validation happens in the POST request
    origin = request.headers.get("origin", "*")

    return _cors_response(origin, "POST, OPTIONS")


@router.get("/turn-credentials/{session_token}", response_model=TurnCredentialsResponse)
async def get_public_turn_credentials(
    session_token: str, request: Request, response: Response
):
    """Get TURN credentials for an embed session.

    This endpoint allows embedded widgets to obtain TURN server credentials
    for WebRTC connections without requiring authentication.

    Args:
        session_token: The session token from embed initialization

    Returns:
        TurnCredentialsResponse with username, password, ttl, and TURN URIs
    """
    origin = get_request_origin(request)

    # Validate session token
    embed_session = await db_client.get_embed_session_by_token(session_token)
    if not embed_session:
        raise HTTPException(status_code=404, detail="Invalid session token")

    # Check if session is expired
    if embed_session.expires_at and embed_session.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=403, detail="Session expired")

    # Get the embed token to check allowed domains
    embed_token = await db_client.get_embed_token_by_id(embed_session.embed_token_id)
    if not embed_token:
        raise HTTPException(status_code=404, detail="Invalid embed token")

    # Validate domain (empty allowed_domains means allow all)
    if not validate_origin(origin, embed_token.allowed_domains or []):
        logger.warning(
            f"Domain validation failed for TURN credentials: {origin} not in {embed_token.allowed_domains}"
        )
        raise HTTPException(status_code=403, detail=f"Domain not allowed: {origin}")

    if origin:
        _allow_embed_origin(response, origin)

    # Check if TURN is configured
    if not TURN_SECRET:
        raise HTTPException(
            status_code=503,
            detail="TURN server not configured",
        )

    try:
        # Use session token as identifier for TURN credentials
        credentials = generate_turn_credentials(f"embed:{session_token[:16]}")
        return TurnCredentialsResponse(**credentials)
    except Exception as e:
        logger.error(f"Failed to generate TURN credentials for embed session: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to generate TURN credentials",
        )


@router.options("/turn-credentials/{session_token}")
async def options_turn_credentials(request: Request, session_token: str):
    """Fallback OPTIONS handler for TURN credentials endpoint."""
    # Browser preflights are handled by PublicEmbedCORSMiddleware before global CORS.
    return await _turn_credentials_preflight_response(
        session_token, request.headers.get("origin", "")
    )
