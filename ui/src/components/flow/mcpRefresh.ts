import { refreshMcpToolsApiV1ToolsToolUuidMcpRefreshPost } from "@/client/sdk.gen";
import type { McpRefreshResponse } from "@/client/types.gen";

export interface McpDiscoveredTool {
    name: string;
    description: string;
}

export interface McpRefreshResult {
    tool_uuid: string;
    discovered_tools: McpDiscoveredTool[];
    error: string | null;
}

function normalizeDiscoveredTools(
    discoveredTools: McpRefreshResponse["discovered_tools"],
): McpDiscoveredTool[] {
    if (!Array.isArray(discoveredTools)) {
        return [];
    }

    return discoveredTools.flatMap((tool) => {
        if (!tool || typeof tool !== "object") {
            return [];
        }

        const name = "name" in tool ? tool.name : undefined;
        if (typeof name !== "string" || !name.trim()) {
            return [];
        }

        const description =
            "description" in tool && typeof tool.description === "string"
                ? tool.description
                : "";

        return [{ name, description }];
    });
}

/**
 * Re-discover an MCP tool's server catalog.
 * Uses the shared generated `client` (auth bearer is injected by interceptor).
 */
export async function refreshMcpTools(
    toolUuid: string,
): Promise<McpRefreshResult> {
    const { data, error } = await refreshMcpToolsApiV1ToolsToolUuidMcpRefreshPost({
        path: {
            tool_uuid: toolUuid,
        },
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
    return {
        tool_uuid: data.tool_uuid,
        discovered_tools: normalizeDiscoveredTools(data.discovered_tools),
        error: data.error ?? null,
    };
}
