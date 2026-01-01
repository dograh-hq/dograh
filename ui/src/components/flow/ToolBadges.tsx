"use client";

import { useCallback, useEffect, useState } from "react";

import { listToolsApiV1ToolsGet } from "@/client/sdk.gen";
import type { ToolResponse } from "@/client/types.gen";
import { Badge } from "@/components/ui/badge";
import { useAuth } from "@/lib/auth";

interface ToolBadgesProps {
    toolUuids: string[];
}

export function ToolBadges({ toolUuids }: ToolBadgesProps) {
    const { getAccessToken } = useAuth();
    const [tools, setTools] = useState<ToolResponse[]>([]);

    const fetchTools = useCallback(async () => {
        try {
            const accessToken = await getAccessToken();
            const response = await listToolsApiV1ToolsGet({
                headers: { Authorization: `Bearer ${accessToken}` },
            });
            if (response.data) {
                setTools(response.data);
            }
        } catch (error) {
            console.error("Failed to fetch tools:", error);
        }
    }, [getAccessToken]);

    useEffect(() => {
        if (toolUuids.length > 0) {
            fetchTools();
        }
    }, [toolUuids.length, fetchTools]);

    const selectedTools = tools.filter((tool) => toolUuids.includes(tool.tool_uuid));

    if (selectedTools.length === 0 && toolUuids.length > 0) {
        // Still loading or tools not found
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
