from fastapi import HTTPException
from fastmcp.server.dependencies import get_http_headers

from api.db.models import UserModel
from api.services.auth.depends import _handle_api_key_auth


async def authenticate_mcp_request() -> UserModel:
    """Resolve the authenticated Dograh user for an MCP tool invocation.

    Accepts either `X-API-Key: <key>` or `Authorization: Bearer <key>`,
    reusing the API-key flow from `api.services.auth.depends`.
    """
    headers = get_http_headers()
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
    return await _handle_api_key_auth(api_key)
