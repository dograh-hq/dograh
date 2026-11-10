"""Load the server only when requested, so bridge/service imports stay independent."""

__all__ = ["mcp"]


def __getattr__(name):
    if name == "mcp":
        from api.mcp_server.server import mcp

        return mcp
    raise AttributeError(name)
