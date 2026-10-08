from contextvars import ContextVar

from fastapi import HTTPException
from fastmcp.server.dependencies import get_http_headers
from opentelemetry import trace

from api.db.models import UserModel
from api.services.auth.depends import _handle_api_key_auth

# Set only by the authenticated, in-process builder relay. Never populated from
# request headers or model arguments; ContextVar keeps concurrent users isolated.
builder_mcp_user: ContextVar[UserModel | None] = ContextVar(
    "builder_mcp_user", default=None
)


async def authenticate_mcp_request() -> UserModel:
    """Resolve the authenticated Dograh user for an MCP tool invocation.

    Accepts either `X-API-Key: <key>` or `Authorization: Bearer <key>`,
    reusing the API-key flow from `api.services.auth.depends`.

    Tags the active span with the authenticated identity. MCP authoring spans
    use the default developer-facing project, not the per-org call exporter.
    """
    if (user := builder_mcp_user.get()) is not None:
        _stamp_identity(user)
        return user
    # FastMCP strips Authorization by default unless explicitly included.
    # Preserve it here so Bearer API keys work for MCP tool invocations.
    headers = get_http_headers(include={"authorization"})
    api_key = headers.get("x-api-key")
    if not api_key:
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            api_key = auth.split(" ", 1)[1].strip()
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing API key — send X-API-Key or Authorization: Bearer <key>",
        )
    user = await _handle_api_key_auth(api_key)

    _stamp_identity(user)
    return user


def _stamp_identity(user: UserModel) -> None:
    span = trace.get_current_span()
    if span.is_recording():
        org_id = user.selected_organization_id
        # Intentionally NOT `dograh.org_id` — that attribute triggers the
        # per-org Langfuse routing for pipeline spans, and MCP traffic
        # should land in the default (developer-facing) project only.
        # Exposed under `mcp.org_id` for Langfuse UI filtering without
        # affecting the router.
        span.set_attribute("mcp.org_id", str(org_id))
        span.set_attribute("mcp.user_id", str(user.id))
        span.set_attribute("user.id", str(user.id))
