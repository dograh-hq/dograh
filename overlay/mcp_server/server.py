"""voice-engine MCP server skeleton.

Phase 0 stub. Stream S5 will wire this into Dograh's SDK + Pipecat so the
platform api can drive flows via MCP tools.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("voice-engine")


@mcp.tool()
def health() -> dict:
    """Return engine status."""
    return {"status": "ok", "service": "voice-engine"}


if __name__ == "__main__":
    mcp.run()
