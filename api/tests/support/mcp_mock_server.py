"""A real FastMCP server exposing 2 tools over streamable-HTTP, run in a
background uvicorn thread on an ephemeral port. Used to exercise the real
MCP protocol path in tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
from typing import AsyncIterator

import httpx
import uvicorn
from fastmcp import FastMCP
from starlette.responses import JSONResponse


def _build_app(required_headers: dict[str, str] | None = None):
    mcp = FastMCP("mock-mcp")

    @mcp.tool()
    def echo(text: str) -> str:
        """Echo the provided text back."""
        return f"echo:{text}"

    @mcp.tool()
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    # FastMCP 3.x: ASGI app for streamable-HTTP transport at "/mcp".
    app = mcp.http_app()
    if not required_headers:
        return app

    normalized = {k.lower(): v for k, v in required_headers.items()}

    async def guarded_app(scope, receive, send):
        if scope["type"] == "http":
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope.get("headers", [])
            }
            for header_name, expected_value in normalized.items():
                if headers.get(header_name) != expected_value:
                    response = JSONResponse(
                        {"detail": f"Missing or invalid header: {header_name}"},
                        status_code=401,
                    )
                    await response(scope, receive, send)
                    return
        await app(scope, receive, send)

    return guarded_app


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.asynccontextmanager
async def running_mcp_server(
    *, required_headers: dict[str, str] | None = None
) -> AsyncIterator[str]:
    """Yield the base streamable-HTTP URL of a live mock MCP server."""
    port = _free_port()
    config = uvicorn.Config(
        _build_app(required_headers), host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}/mcp"
    server_ready = False
    for _ in range(50):
        try:
            async with httpx.AsyncClient() as client:
                await client.get(base_url, timeout=0.5)
            server_ready = True
            break
        except Exception:
            await asyncio.sleep(0.1)
    if not server_ready:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError(f"Mock MCP server at {base_url} failed to start within 5s")
    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            import warnings

            warnings.warn(
                "Mock MCP server thread did not terminate within 5s",
                ResourceWarning,
            )
