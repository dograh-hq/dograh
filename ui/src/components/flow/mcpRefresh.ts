import { client } from "@/client/client.gen";

export interface McpDiscoveredTool {
    name: string;
    description: string;
}

export interface McpRefreshResult {
    tool_uuid: string;
    discovered_tools: McpDiscoveredTool[];
    error: string | null;
}

/**
 * Re-discover an MCP tool's server catalog.
 * Uses the shared generated `client` (auth bearer is injected by interceptor).
 */
export async function refreshMcpTools(
    toolUuid: string,
): Promise<McpRefreshResult> {
    const { data, error } = await client.post({
        url: `/api/v1/tools/${toolUuid}/mcp/refresh`,
    });
    if (error || !data) {
        return {
            tool_uuid: toolUuid,
            discovered_tools: [],
            error:
                typeof error === "string"
                    ? error
                    : "Refresh request failed. Check the MCP server and try again.",
        };
    }
    return data as McpRefreshResult;
}
