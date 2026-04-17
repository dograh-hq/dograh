from fastmcp import FastMCP

mcp = FastMCP("dograh")

from api.mcp.tools import catalog as _catalog  # noqa: E402, F401
from api.mcp.tools import docs as _docs  # noqa: E402, F401
from api.mcp.tools import node_types as _node_types  # noqa: E402, F401
from api.mcp.tools import workflows as _workflows  # noqa: E402, F401
