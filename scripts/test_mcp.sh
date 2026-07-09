#!/usr/bin/env bash
# Test an MCP server connection — discover tools without going through an agent.
# Usage: ./scripts/test_mcp.sh <MCP_SERVER_URL>
set -e

BASE_DIR="$(cd "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" && pwd)"
cd "$BASE_DIR"

if [ -z "$1" ]; then
    echo "Usage: $0 <MCP_SERVER_URL>"
    echo "Example: $0 https://serviceapiwizer.topcs.it/mcp/server/WB5CUzmGHkRxGxMB/mcp"
    exit 1
fi

URL="$1"

source venv/bin/activate
set -a && source api/.env && set +a

python -c "
import asyncio
from api.services.workflow.mcp_tool_session import discover_mcp_tools

async def test():
    print(f'MCP URL: $URL')
    print()
    tools = await discover_mcp_tools(
        url='$URL',
        credential=None,
        timeout_secs=10,
        sse_read_timeout_secs=10,
    )
    print(f'\033[32mConnected! Discovered {len(tools)} tool(s):\033[0m')
    print()
    if not tools:
        print('  (no tools exposed by this MCP server)')
    for t in tools:
        print(f'  \033[1m• {t[\"name\"]}\033[0m')
        desc = t.get('description', '')
        if desc:
            print(f'    {desc[:120]}')
        print()

asyncio.run(test())
" 2>&1 | grep -v "^2026\|SCTP\|storage\|connection\|pipecat\|DEBUG\|INFO\|Selector"
