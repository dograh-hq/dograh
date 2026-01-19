"use client";

import { useCallback, useEffect, useState } from "react";

import { useWorkflow } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import type { ToolResponse } from "@/client/types.gen";
import { Badge } from "@/components/ui/badge";

interface ToolBadgesProps {
    toolUuids: string[];
    onStaleUuidsDetected?: (staleUuids: string[]) => void;
}

export function ToolBadges({ toolUuids, onStaleUuidsDetected }: ToolBadgesProps) {
    const { tools } = useWorkflow();
    const [selectedTools, setSelectedTools] = useState<ToolResponse[]>([]);

    const processTools = useCallback((toolsData: ToolResponse[]) => {
        const filtered = toolsData.filter(tool => toolUuids.includes(tool.tool_uuid));
        setSelectedTools(filtered);

        // Detect stale UUIDs - this only runs when we have loaded data (not undefined)
        if (onStaleUuidsDetected) {
            const validUuids = new Set(toolsData.map(tool => tool.tool_uuid));
            const staleUuids = toolUuids.filter(uuid => !validUuids.has(uuid));
            if (staleUuids.length > 0) {
                onStaleUuidsDetected(staleUuids);
            }
        }
    }, [toolUuids, onStaleUuidsDetected]);

    useEffect(() => {
        if (toolUuids.length > 0 && tools !== undefined) {
            processTools(tools);
        } else if (toolUuids.length === 0) {
            setSelectedTools([]);
        }
    }, [toolUuids, tools, processTools]);

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
            {selectedTools.map((tool) => (
                <Badge
                    key={tool.tool_uuid}
                    variant="outline"
                    className="text-xs"
                >
                    {tool.name}
                </Badge>
            ))}
        </div>
    );
}
