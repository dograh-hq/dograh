"use client";

import { ExternalLink, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { renderToolIcon } from "@/app/tools/config";
import { useWorkflowOptional } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import type { ToolResponse } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TOOLS_INTRODUCTION_DOC_URL } from "@/constants/documentation";

import { type McpDiscoveredTool, refreshMcpTools } from "./mcpRefresh";

interface ToolSelectorProps {
    value: string[];
    onChange: (uuids: string[]) => void;
    tools: ToolResponse[];
    disabled?: boolean;
    label?: string;
    description?: string;
    showLabel?: boolean;
    mcpToolFilters?: Record<string, string[]>;
    onMcpToolFiltersChange?: (next: Record<string, string[]>) => void;
}

function isMcp(tool: ToolResponse): boolean {
    return tool.category === "mcp";
}

function discoveredOf(tool: ToolResponse): McpDiscoveredTool[] {
    const def = (tool.definition ?? {}) as {
        config?: { discovered_tools?: McpDiscoveredTool[] };
    };
    return def.config?.discovered_tools ?? [];
}

function withDiscoveredTools(
    tool: ToolResponse,
    discoveredTools: McpDiscoveredTool[],
): ToolResponse {
    const definition =
        tool.definition && typeof tool.definition === "object"
            ? tool.definition
            : {};
    const config =
        "config" in definition &&
        definition.config &&
        typeof definition.config === "object"
            ? definition.config
            : {};

    return {
        ...tool,
        definition: {
            ...definition,
            config: {
                ...config,
                discovered_tools: discoveredTools,
            },
        },
    };
}

export function ToolSelector({
    value,
    onChange,
    tools,
    disabled = false,
    label = "Tools",
    description = "Select tools that the agent can use during the conversation.",
    showLabel = true,
    mcpToolFilters = {},
    onMcpToolFiltersChange = () => {},
}: ToolSelectorProps) {
    const workflow = useWorkflowOptional();
    const activeTools = tools.filter((t) => t.status === "active");
    const httpTools = activeTools.filter((t) => !isMcp(t));
    const mcpTools = activeTools.filter(isMcp);

    const [refreshing, setRefreshing] = useState<Record<string, boolean>>({});
    const [refreshError, setRefreshError] = useState<Record<string, string>>({});

    const httpHandleToggle = (toolUuid: string, checked: boolean) => {
        if (checked) onChange([...value, toolUuid]);
        else onChange(value.filter((id) => id !== toolUuid));
    };

    const mcpFnToggle = (toolUuid: string, fnName: string, checked: boolean) => {
        const current = mcpToolFilters[toolUuid] ?? [];
        const nextFns = checked
            ? Array.from(new Set([...current, fnName]))
            : current.filter((n) => n !== fnName);

        const nextFilters = { ...mcpToolFilters };
        if (nextFns.length > 0) nextFilters[toolUuid] = nextFns;
        else delete nextFilters[toolUuid];
        onMcpToolFiltersChange(nextFilters);

        const hasUuid = value.includes(toolUuid);
        if (nextFns.length > 0 && !hasUuid) onChange([...value, toolUuid]);
        else if (nextFns.length === 0 && hasUuid)
            onChange(value.filter((id) => id !== toolUuid));
    };

    const doRefresh = async (toolUuid: string) => {
        setRefreshing((r) => ({ ...r, [toolUuid]: true }));
        setRefreshError((e) => {
            const n = { ...e };
            delete n[toolUuid];
            return n;
        });
        const res = await refreshMcpTools(toolUuid);
        setRefreshing((r) => ({ ...r, [toolUuid]: false }));
        if (res.error && res.discovered_tools.length === 0) {
            setRefreshError((e) => ({ ...e, [toolUuid]: res.error as string }));
            return;
        }
        workflow?.updateTool?.(toolUuid, (tool) =>
            withDiscoveredTools(tool, res.discovered_tools),
        );
    };

    const selectedCount =
        httpTools.filter((t) => value.includes(t.tool_uuid)).length +
        mcpTools.reduce(
            (acc, t) => acc + (mcpToolFilters[t.tool_uuid]?.length ?? 0),
            0,
        );

    return (
        <div className="grid gap-2">
            {showLabel && (
                <>
                    <Label>{label}</Label>
                    {description && (
                        <Label className="text-xs text-muted-foreground">
                            {description}{" "}
                            <a
                                href={TOOLS_INTRODUCTION_DOC_URL}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="underline"
                            >
                                Learn more
                            </a>
                        </Label>
                    )}
                </>
            )}

            {activeTools.length === 0 ? (
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
                <Tabs defaultValue="http">
                    <TabsList>
                        <TabsTrigger value="http">
                            HTTP &amp; Tools ({httpTools.length})
                        </TabsTrigger>
                        <TabsTrigger value="mcp">
                            MCP ({mcpTools.length})
                        </TabsTrigger>
                    </TabsList>

                    <TabsContent value="http">
                        <div className="border rounded-md divide-y">
                            {httpTools.length === 0 && (
                                <div className="p-3 text-sm text-muted-foreground">
                                    No HTTP/native tools.
                                </div>
                            )}
                            {httpTools.map((tool) => {
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
                                            onCheckedChange={(c) =>
                                                httpHandleToggle(tool.tool_uuid, c === true)
                                            }
                                        />
                                        <div
                                            className="w-6 h-6 rounded flex items-center justify-center shrink-0"
                                            style={{
                                                backgroundColor: tool.icon_color || "#3B82F6",
                                            }}
                                        >
                                            {renderToolIcon(tool.category, "h-3 w-3 text-white")}
                                        </div>
                                        <div className="flex flex-col min-w-0 flex-1">
                                            <span className="text-sm font-medium truncate">
                                                {tool.name}
                                            </span>
                                            {tool.description && (
                                                <span className="text-xs text-muted-foreground break-words">
                                                    {tool.description}
                                                </span>
                                            )}
                                        </div>
                                    </label>
                                );
                            })}
                        </div>
                    </TabsContent>

                    <TabsContent value="mcp">
                        <div className="border rounded-md divide-y">
                            {mcpTools.length === 0 && (
                                <div className="p-3 text-sm text-muted-foreground">
                                    No MCP tools.
                                </div>
                            )}
                            {mcpTools.map((tool) => {
                                const fns = discoveredOf(tool);
                                const selected = mcpToolFilters[tool.tool_uuid] ?? [];
                                const busy = !!refreshing[tool.tool_uuid];
                                const err = refreshError[tool.tool_uuid];
                                return (
                                    <details key={tool.tool_uuid} className="p-3">
                                        <summary className="flex items-center gap-3 cursor-pointer list-none">
                                            <div
                                                className="w-6 h-6 rounded flex items-center justify-center shrink-0"
                                                style={{
                                                    backgroundColor: tool.icon_color || "#8B5CF6",
                                                }}
                                            >
                                                {renderToolIcon(tool.category, "h-3 w-3 text-white")}
                                            </div>
                                            <div className="flex flex-col min-w-0 flex-1">
                                                <span className="text-sm font-medium truncate">
                                                    {tool.name}
                                                </span>
                                                {tool.description && (
                                                    <span className="text-xs text-muted-foreground break-words">
                                                        {tool.description}
                                                    </span>
                                                )}
                                            </div>
                                            <span className="text-xs text-muted-foreground shrink-0">
                                                {selected.length}/{fns.length} tools
                                            </span>
                                        </summary>

                                        <div className="mt-3 pl-9 grid gap-2">
                                            <div>
                                                <Button
                                                    type="button"
                                                    variant="outline"
                                                    size="sm"
                                                    disabled={busy}
                                                    onClick={() => doRefresh(tool.tool_uuid)}
                                                >
                                                    <RefreshCw
                                                        className={`h-3 w-3 mr-2 ${busy ? "animate-spin" : ""}`}
                                                    />
                                                    Refresh tools
                                                </Button>
                                            </div>
                                            {err && (
                                                <p className="text-xs text-destructive">{err}</p>
                                            )}
                                            {fns.length === 0 && !err && (
                                                <p className="text-xs text-muted-foreground">
                                                    No tools discovered — Refresh.
                                                </p>
                                            )}
                                            {fns.map((fn) => {
                                                const checked = selected.includes(fn.name);
                                                return (
                                                    <label
                                                        key={fn.name}
                                                        className="flex items-start gap-3 cursor-pointer"
                                                    >
                                                        <Checkbox
                                                            checked={checked}
                                                            disabled={disabled}
                                                            onCheckedChange={(c) =>
                                                                mcpFnToggle(tool.tool_uuid, fn.name, c === true)
                                                            }
                                                        />
                                                        <div className="flex flex-col min-w-0 flex-1">
                                                            <span className="text-sm font-medium">
                                                                {fn.name}
                                                            </span>
                                                            {fn.description && (
                                                                <span className="text-xs text-muted-foreground break-words">
                                                                    {fn.description}
                                                                </span>
                                                            )}
                                                        </div>
                                                    </label>
                                                );
                                            })}
                                            {selected
                                                .filter((n) => !fns.some((f) => f.name === n))
                                                .map((n) => (
                                                    <label
                                                        key={`stale-${n}`}
                                                        className="flex items-start gap-3 cursor-pointer opacity-60"
                                                    >
                                                        <Checkbox
                                                            checked
                                                            disabled={disabled}
                                                            onCheckedChange={() =>
                                                                mcpFnToggle(tool.tool_uuid, n, false)
                                                            }
                                                        />
                                                        <span className="text-sm line-through">
                                                            {n} (unavailable)
                                                        </span>
                                                    </label>
                                                ))}
                                        </div>
                                    </details>
                                );
                            })}
                        </div>
                    </TabsContent>

                    <div className="mt-2 p-2 bg-muted/30 rounded-md">
                        <Link
                            href="/tools"
                            target="_blank"
                            className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
                        >
                            <ExternalLink className="h-4 w-4" />
                            Manage Tools
                        </Link>
                    </div>
                </Tabs>
            )}

            {selectedCount > 0 && (
                <p className="text-xs text-muted-foreground">
                    {selectedCount} tool{selectedCount !== 1 ? "s" : ""} selected
                </p>
            )}
        </div>
    );
}
