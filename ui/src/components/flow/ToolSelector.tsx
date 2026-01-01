"use client";

import { ExternalLink, Globe, Loader2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { listToolsApiV1ToolsGet } from "@/client/sdk.gen";
import type { ToolResponse } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/lib/auth";

interface ToolSelectorProps {
    value: string[];
    onChange: (uuids: string[]) => void;
    disabled?: boolean;
    label?: string;
    description?: string;
    showLabel?: boolean;
}

export function ToolSelector({
    value,
    onChange,
    disabled = false,
    label = "Tools",
    description = "Select tools that the agent can use during the conversation.",
    showLabel = true,
}: ToolSelectorProps) {
    const { getAccessToken } = useAuth();

    const [tools, setTools] = useState<ToolResponse[]>([]);
    const [loading, setLoading] = useState(false);

    const fetchTools = useCallback(async () => {
        setLoading(true);
        try {
            const accessToken = await getAccessToken();
            const response = await listToolsApiV1ToolsGet({
                headers: { Authorization: `Bearer ${accessToken}` },
                query: { status: "active" },
            });
            if (response.error) {
                console.error("Failed to fetch tools:", response.error);
                setTools([]);
                return;
            }
            if (response.data) {
                setTools(response.data);
            }
        } catch (error) {
            console.error("Failed to fetch tools:", error);
            setTools([]);
        } finally {
            setLoading(false);
        }
    }, [getAccessToken]);

    useEffect(() => {
        fetchTools();
    }, [fetchTools]);

    const handleToggle = (toolUuid: string, checked: boolean) => {
        if (checked) {
            onChange([...value, toolUuid]);
        } else {
            onChange(value.filter((id) => id !== toolUuid));
        }
    };

    return (
        <div className="grid gap-2">
            {showLabel && (
                <>
                    <Label>{label}</Label>
                    {description && (
                        <Label className="text-xs text-muted-foreground">
                            {description}
                        </Label>
                    )}
                </>
            )}

            {loading ? (
                <div className="flex items-center gap-2 p-3 border rounded-md">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    <span className="text-sm text-muted-foreground">Loading tools...</span>
                </div>
            ) : tools.length === 0 ? (
                <div className="p-4 border rounded-md text-center">
                    <p className="text-sm text-muted-foreground mb-2">
                        No tools available.
                    </p>
                    <Button variant="outline" size="sm" asChild>
                        <Link href="/tools" target="_blank">
                            <ExternalLink className="h-4 w-4 mr-2" />
                            Create a Tool
                        </Link>
                    </Button>
                </div>
            ) : (
                <div className="border rounded-md divide-y">
                    {tools.map((tool) => {
                        const isSelected = value.includes(tool.tool_uuid);
                        return (
                            <label
                                key={tool.tool_uuid}
                                className={`flex items-center gap-3 p-3 cursor-pointer hover:bg-muted/50 ${
                                    disabled ? "opacity-50 cursor-not-allowed" : ""
                                }`}
                            >
                                <Checkbox
                                    checked={isSelected}
                                    disabled={disabled}
                                    onCheckedChange={(checked) => {
                                        handleToggle(tool.tool_uuid, checked === true);
                                    }}
                                />
                                <div
                                    className="w-6 h-6 rounded flex items-center justify-center shrink-0"
                                    style={{
                                        backgroundColor: tool.icon_color || "#3B82F6",
                                    }}
                                >
                                    <Globe className="h-3 w-3 text-white" />
                                </div>
                                <div className="flex flex-col min-w-0 flex-1">
                                    <span className="text-sm font-medium truncate">
                                        {tool.name}
                                    </span>
                                    {tool.description && (
                                        <span className="text-xs text-muted-foreground truncate">
                                            {tool.description}
                                        </span>
                                    )}
                                </div>
                            </label>
                        );
                    })}
                    <div className="p-2 bg-muted/30">
                        <Link
                            href="/tools"
                            target="_blank"
                            className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
                        >
                            <ExternalLink className="h-4 w-4" />
                            Manage Tools
                        </Link>
                    </div>
                </div>
            )}

            {value.length > 0 && (
                <p className="text-xs text-muted-foreground">
                    {value.length} tool{value.length !== 1 ? "s" : ""} selected
                </p>
            )}
        </div>
    );
}
