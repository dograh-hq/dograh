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
from pipecat.utils.run_context import set_current_run_id
from pydantic import BaseModel
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send

from api.constants import ENABLE_COTURN, FORCE_TURN_RELAY
from api.db import db_client
from api.enums import WorkflowRunMode
from api.routes.turn_credentials import (
    TURN_SECRET,
    TurnCredentialsResponse,
    generate_turn_credentials,
)
from api.schemas.embed_chat import PublicEmbedChatSessionResponse
from api.services.workflow.embed_chat_limiter import allow_embed_chat_init
from api.services.workflow.embed_context import sanitize_embed_context_variables
from api.services.workflow.embed_session_service import (
    EmbedSessionNotFoundError,
    EmbedSessionValidationError,
    EmbedTokenNotFoundError,
    authorize_embed_workflow_run_start,
    resolve_embed_session,
    validate_embed_origin,
)
from api.services.workflow.embed_text_chat_service import (
    build_public_chat_session_response,
    start_embed_text_chat,
)
from api.services.workflow.run_creation import prepare_workflow_run_inputs
from api.services.workflow.text_chat_session_service import (
    TextChatPendingTurnLostError,
    TextChatSessionExecutionError,
    TextChatSessionRevisionConflictError,
)

router = APIRouter(prefix="/public/embed")

EMBED_CORS_ALLOW_HEADERS = "Content-Type, Origin"
EMBED_CORS_MAX_AGE = "86400"


def _turn_credentials_available() -> bool:
    """Return whether the public endpoint can mint TURN credentials."""
    return ENABLE_COTURN and bool(TURN_SECRET)


class InitEmbedRequest(BaseModel):
    """Request model for initializing an embed session"""

    token: str
    context_variables: Optional[dict] = None


class InitEmbedResponse(BaseModel):
    """Response model for embed initialization"""

    session_token: str
    workflow_run_id: int
    config: dict
    widget_type: str = "voice"
    # For chat widgets the greeting turn runs synchronously during init, so the
    # opening transcript rides along and the widget skips a fetch.
    chat_session: PublicEmbedChatSessionResponse | None = None


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
    # WebRTC transport hints, mirroring the same fields on /health. The embed
    # widget is a standalone script on a third-party page — it has no access to
    # the first-party app config that /health feeds — so these ride along with
    # the embed config. turn_enabled=False lets the widget skip the
    # turn-credentials request that would only 503; force_turn_relay tells it to
    # restrict ICE to relay candidates for TURN diagnostics.
    turn_enabled: bool
    force_turn_relay: bool


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

    if not validate_embed_origin(origin, embed_token.allowed_domains or []):
        return Response(status_code=403)

    return _cors_response(origin, "GET, OPTIONS")


async def _session_preflight_response(
    session_token: str, origin: str, methods: str
) -> Response:
    """Preflight for session-scoped endpoints gates on ORIGIN only.

    Session-state problems (unknown/expired session, inactive token) must NOT
    fail the preflight: the browser would surface them to the page as an opaque
    network error. Letting the preflight succeed lets the real request return a
    readable 4xx (PublicEmbedCORSMiddleware reflects ACAO onto embed responses,
    including error responses).
    """
    embed_session = await db_client.get_embed_session_by_token(session_token)
    if embed_session:
        embed_token = await db_client.get_embed_token_by_id(
            embed_session.embed_token_id
        )
        if embed_token and not validate_embed_origin(
            origin, embed_token.allowed_domains or []
        ):
            return Response(status_code=403)

    return _cors_response(origin, methods)


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
        return await _session_preflight_response(session_token, origin, "GET, OPTIONS")

    chat_prefix = f"{public_embed_prefix}/chat/"
    if path.startswith(chat_prefix):
        if requested_method.upper() not in ("GET", "POST"):
            return Response(status_code=405)
        session_token = path[len(chat_prefix) :].split("/", 1)[0]
        return await _session_preflight_response(
            session_token, origin, "GET, POST, OPTIONS"
        )

    return None


class PublicEmbedCORSMiddleware:
    """Allow token-gated embed CORS before global SaaS CORS rejects preflights."""

    def __init__(self, app: ASGIApp, api_prefix: str = "/api/v1"):
        self.app = app
        self.api_prefix = api_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        origin = headers.get("origin")

        if scope.get("method") == "OPTIONS":
            requested_method = headers.get("access-control-request-method")
            if origin and requested_method:
                response = await build_public_embed_preflight_response(
                    scope.get("path", ""), origin, requested_method, self.api_prefix
                )
                if response is not None:
                    await response(scope, receive, send)
                    return
            await self.app(scope, receive, send)
            return

        embed_prefix = f"{self.api_prefix.rstrip('/')}/public/embed"
        if origin and scope.get("path", "").startswith(embed_prefix):
            # Reflect ACAO on every embed response, including error responses.
            # Routes set ACAO on success via _allow_embed_origin, but raised
            # HTTPExceptions bypass the injected Response object, so without
            # this a third-party page can't read e.g. a 403 session-expired and
            # sees an opaque CORS network error instead. Success data stays
            # origin-gated in-route by validate_embed_origin — this only makes error
            # statuses readable.
            async def send_with_cors(message) -> None:
                if message["type"] == "http.response.start":
                    response_headers = MutableHeaders(
                        raw=message.setdefault("headers", [])
                    )
                    if "access-control-allow-origin" not in response_headers:
                        response_headers.append("Access-Control-Allow-Origin", origin)
                        vary = response_headers.get("Vary")
                        if not vary:
                            response_headers.append("Vary", "Origin")
                        elif "origin" not in {
                            value.strip().lower() for value in vary.split(",")
                        }:
                            response_headers["Vary"] = f"{vary}, Origin"
                await send(message)

            await self.app(scope, receive, send_with_cors)
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

    # Validate domain
    if not validate_embed_origin(origin, embed_token.allowed_domains or []):
        logger.warning(
            f"Domain validation failed: {origin} not in {embed_token.allowed_domains}"
        )
        raise HTTPException(status_code=403, detail=f"Domain not allowed: {origin}")

    if origin:
        _allow_embed_origin(response, origin)

    # Widget type is derived from the embed token's settings — never from the
    # request — so a client can't turn a voice token into a chat session.
    widget_type = (
        "chat" if (embed_token.settings or {}).get("widgetType") == "chat" else "voice"
    )
    is_chat = widget_type == "chat"

    # Chat initialization immediately performs billable greeting work. Keep it
    # in an isolated token-scoped rate bucket so anonymous bursts cannot fan
    # out unbounded LLM work, without consuming organization voice-call slots.
    if is_chat and not await allow_embed_chat_init(embed_token.id):
        raise HTTPException(
            status_code=429, detail="Too many chat sessions. Please try again shortly"
        )

    # This conditional update is the authoritative usage-limit check. Keeping
    # the comparison and increment in one statement prevents parallel public
    # requests from all passing a stale in-memory usage_count check.
    usage_reserved = await db_client.reserve_embed_token_usage(
        embed_token.id, embed_token.organization_id
    )
    if not usage_reserved:
        raise HTTPException(status_code=403, detail="Embed token usage limit exceeded")

    # Create workflow run
    try:
        workflow = await db_client.get_workflow(
            embed_token.workflow_id, organization_id=embed_token.organization_id
        )
        if not workflow:
            raise ValueError("Workflow not found")
        run_inputs = await prepare_workflow_run_inputs(db_client, workflow)
        # Visitor context comes from the host page (script URL params or the
        # data-dograh-context attribute) and is addressable from prompts as
        # {{initial_context.<name>}}.
        context_variables = sanitize_embed_context_variables(
            init_request.context_variables
        )
        if is_chat:
            mode = WorkflowRunMode.TEXTCHAT.value
            name = f"Embed Chat - {datetime.now(UTC).isoformat()}"
            # Text-chat runs carry no transport provider (mirrors the
            # authenticated text-chat route).
            initial_context = {**context_variables}
        else:
            mode = WorkflowRunMode.SMALLWEBRTC.value
            name = f"Embed Run - {datetime.now(UTC).isoformat()}"
            initial_context = {
                **context_variables,
                "provider": WorkflowRunMode.SMALLWEBRTC.value,
            }
        workflow_run = await db_client.create_workflow_run(
            name=name,
            workflow_id=embed_token.workflow_id,
            mode=mode,
            user_id=embed_token.created_by,  # Use token creator as run owner
            organization_id=embed_token.organization_id,
            initial_context=initial_context,
            definition_id=run_inputs.definition_id,
        )
        if is_chat:
            workflow_run = await db_client.update_workflow_run(
                workflow_run.id,
                annotations={"embed": {"source": "embed_widget", "modality": "text"}},
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

    # For chat widgets, seed the text session and run the greeting turn
    # synchronously so the widget opens with the agent's first message. Quota is
    # checked first — before any LLM spend — mirroring the authenticated
    # text-chat create flow. (Voice runs are quota-checked later, on the
    # signaling WebSocket.)
    chat_session = None
    if is_chat:
        set_current_run_id(workflow_run.id)
        quota_result = await authorize_embed_workflow_run_start(
            embed_token=embed_token,
            workflow_run_id=workflow_run.id,
        )
        if not quota_result.has_quota:
            raise HTTPException(
                status_code=402, detail="The agent is unavailable right now"
            )
        try:
            text_session = await start_embed_text_chat(
                workflow_id=embed_token.workflow_id, run_id=workflow_run.id
            )
        except (
            TextChatSessionRevisionConflictError,
            TextChatPendingTurnLostError,
            TextChatSessionExecutionError,
        ) as e:
            # Public surface: log the specifics, return a generic detail.
            logger.error(f"Embed chat greeting failed for run {workflow_run.id}: {e}")
            raise HTTPException(status_code=500, detail="Assistant failed to respond")
        chat_session = build_public_chat_session_response(text_session)

    # Prepare configuration
    config = {
        "workflow_id": embed_token.workflow_id,
        "workflow_run_id": workflow_run.id,
        **(embed_token.settings or {}),
    }

    return InitEmbedResponse(
        session_token=session_token,
        workflow_run_id=workflow_run.id,
        config=config,
        widget_type=widget_type,
        chat_session=chat_session,
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
    if not validate_embed_origin(origin, embed_token.allowed_domains or []):
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
        turn_enabled=_turn_credentials_available(),
        force_turn_relay=FORCE_TURN_RELAY,
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

    try:
        await resolve_embed_session(session_token, origin)
    except (EmbedSessionNotFoundError, EmbedTokenNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except EmbedSessionValidationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    if origin:
        _allow_embed_origin(response, origin)

    # Check if TURN is configured. Both conditions matter: ENABLE_COTURN is what
    # the config endpoint advertised, and without a secret there is nothing to
    # sign credentials with.
    if not _turn_credentials_available():
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
    return await _session_preflight_response(
        session_token, request.headers.get("origin", ""), "GET, OPTIONS"
    )
