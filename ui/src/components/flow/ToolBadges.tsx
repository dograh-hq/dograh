"use client";

import { useWorkflow } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import { Badge } from "@/components/ui/badge";

interface ToolBadgesProps {
    toolUuids: string[];
    mcpToolFilters?: Record<string, string[]>;
}

export function ToolBadges({ toolUuids, mcpToolFilters }: ToolBadgesProps) {
    const { tools } = useWorkflow();
    const toolsByUuid = new Map(tools?.map(tool => [tool.tool_uuid, tool]));

    // Show loading while data hasn't loaded yet
    if (tools === undefined && toolUuids.length > 0) {
        return (
            <div className="flex flex-wrap gap-1">
                <Badge variant="outline" className="text-xs">
                    Loading...
                </Badge>
            </div>
        );
    }

    return (
        <div className="flex flex-wrap gap-1">
            {toolUuids.map((uuid) => {
                const tool = toolsByUuid.get(uuid);
                // The catalog can lag behind builder saves. Missing metadata
                // must never remove a binding from the workflow.
                if (!tool) {
                    return <Badge key={uuid} variant="outline" className="text-xs" title={uuid}>Tool unavailable</Badge>;
                }
                const isMcp = tool.category === "mcp";
                const enabledFns = isMcp ? (mcpToolFilters?.[tool.tool_uuid] ?? []) : [];

                if (isMcp && enabledFns.length > 0) {
                    return enabledFns.map((fn) => (
                        <Badge
                            key={`${tool.tool_uuid}-${fn}`}
                            variant="outline"
                            className="text-xs flex items-center gap-1.5"
                        >
                            <span className="h-1.5 w-1.5 rounded-full bg-green-500 shrink-0" />
                            {fn}
                        </Badge>
                    ));
                }

                return (
                    <Badge key={tool.tool_uuid} variant="outline" className="text-xs">
                        {tool.name}
                    </Badge>
                );
            })}
        </div>
    );
}
