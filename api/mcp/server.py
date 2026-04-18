from fastmcp import FastMCP

from api.mcp.instructions import DOGRAH_MCP_INSTRUCTIONS

mcp = FastMCP("dograh", instructions=DOGRAH_MCP_INSTRUCTIONS)

from api.mcp.tools import catalog as _catalog  # noqa: E402, F401
from api.mcp.tools import get_workflow_code as _get_workflow_code  # noqa: E402, F401
from api.mcp.tools import node_types as _node_types  # noqa: E402, F401
from api.mcp.tools import save_workflow as _save_workflow  # noqa: E402, F401
from api.mcp.tools import workflows as _workflows  # noqa: E402, F401
