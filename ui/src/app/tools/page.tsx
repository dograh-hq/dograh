"use client";

import { AlertCircle, CheckCircle2, ChevronDown, Download, ExternalLink, FileJson, Plus, RotateCcw, Search, Trash2, Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import {
    createToolApiV1ToolsPost,
    deleteToolApiV1ToolsToolUuidDelete,
    importToolsApiV1ToolsImportPost,
    listToolsApiV1ToolsGet,
    unarchiveToolApiV1ToolsToolUuidUnarchivePost,
} from "@/client/sdk.gen";
import type { CreateToolRequest, ImportToolError, ToolResponse } from "@/client/types.gen";
import { CredentialSelector } from "@/components/http";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

import {
    createMcpDefinition,
    createToolDefinition,
    getCategoryConfig,
    MCP_URL_PATTERN,
    renderToolIcon,
    TOOL_CATEGORIES,
    type ToolCategory,
} from "./config";

export default function ToolsPage() {
    const { user, getAccessToken, redirectToLogin, loading } = useAuth();
    const router = useRouter();

    const [tools, setTools] = useState<ToolResponse[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [searchQuery, setSearchQuery] = useState("");
    const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
    const [newToolName, setNewToolName] = useState("");
    const [newToolDescription, setNewToolDescription] = useState("");
    const [newToolCategory, setNewToolCategory] = useState<ToolCategory>("http_api");
    const [isCreating, setIsCreating] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [createError, setCreateError] = useState<string | null>(null);

    // MCP-specific create dialog state
    const [mcpUrl, setMcpUrl] = useState("");
    const [mcpCredentialUuid, setMcpCredentialUuid] = useState("");
    const [mcpToolsFilter, setMcpToolsFilter] = useState("");

    // Import tools dialog state
    const [isImportDialogOpen, setIsImportDialogOpen] = useState(false);
    const [selectedFileName, setSelectedFileName] = useState<string | null>(null);
    const [importParsedTools, setImportParsedTools] = useState<Record<string, unknown>[] | null>(null);
    const [importParseError, setImportParseError] = useState<string | null>(null);
    const [isImporting, setIsImporting] = useState(false);
    const [importResult, setImportResult] = useState<{
        importedCount: number;
        errors: ImportToolError[];
    } | null>(null);

    // Redirect if not authenticated
    useEffect(() => {
        if (!loading && !user) {
            redirectToLogin();
        }
    }, [loading, user, redirectToLogin]);

    const fetchTools = useCallback(async () => {
        if (loading || !user) return;

        try {
            setIsLoading(true);
            setError(null);
            const accessToken = await getAccessToken();

            const response = await listToolsApiV1ToolsGet({
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
                query: {
                    status: "active,archived",
                },
            });

            if (response.data) {
                setTools(response.data);
            }
        } catch (err) {
            setError("Failed to fetch tools");
            console.error("Error fetching tools:", err);
        } finally {
            setIsLoading(false);
        }
    }, [loading, user, getAccessToken]);

    useEffect(() => {
        fetchTools();
    }, [fetchTools]);

    const handleCreateTool = async () => {
        if (!newToolName.trim()) {
            setCreateError("Please enter a name for the tool");
            return;
        }

        if (newToolCategory === "mcp" && !mcpUrl.trim()) {
            setCreateError("Please enter the MCP server URL");
            return;
        }

        if (newToolCategory === "mcp" && !MCP_URL_PATTERN.test(mcpUrl.trim())) {
            setCreateError("MCP server URL must start with http:// or https://");
            return;
        }

        try {
            setIsCreating(true);
            setCreateError(null);
            const accessToken = await getAccessToken();

            const categoryConfig = getCategoryConfig(newToolCategory);

            const definition = newToolCategory === "mcp"
                ? createMcpDefinition(mcpUrl, mcpCredentialUuid, mcpToolsFilter)
                : createToolDefinition(newToolCategory);

            const requestBody: CreateToolRequest = {
                name: newToolName,
                description: newToolDescription || undefined,
                category: newToolCategory,
                icon: categoryConfig?.iconName || "globe",
                icon_color: categoryConfig?.iconColor || "#3B82F6",
                definition,
            };

            const response = await createToolApiV1ToolsPost({
                body: requestBody,
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
            });

            if (response.error) {
                setCreateError(detailFromError(response.error, "Failed to create tool"));
                return;
            }

            if (response.data) {
                setIsCreateDialogOpen(false);
                setNewToolName("");
                setNewToolDescription("");
                setNewToolCategory("http_api");
                setMcpUrl("");
                setMcpCredentialUuid("");
                setMcpToolsFilter("");
                // Navigate to the new tool's detail page
                router.push(`/tools/${response.data.tool_uuid}`);
            }
        } catch (err: unknown) {
            let errorMessage = "Failed to create tool";
            if (err && typeof err === "object") {
                const errObj = err as Record<string, unknown>;
                // Handle API client error response
                if (errObj.error && typeof errObj.error === "object") {
                    const errorData = errObj.error as Record<string, unknown>;
                    if (typeof errorData.detail === "string") {
                        errorMessage = errorData.detail;
                    }
                }
                // Handle standard Error objects
                else if (errObj.message && typeof errObj.message === "string") {
                    errorMessage = errObj.message;
                }
            }
            setCreateError(errorMessage);
            console.error("Error creating tool:", err);
        } finally {
            setIsCreating(false);
        }
    };

    const handleDeleteTool = async (toolUuid: string, e: React.MouseEvent) => {
        e.stopPropagation();
        if (!confirm("Are you sure you want to archive this tool?")) return;

        try {
            setError(null);
            const accessToken = await getAccessToken();

            await deleteToolApiV1ToolsToolUuidDelete({
                path: {
                    tool_uuid: toolUuid,
                },
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
            });

            fetchTools();
        } catch (err) {
            setError("Failed to archive tool");
            console.error("Error archiving tool:", err);
        }
    };

    const handleUnarchiveTool = async (toolUuid: string, e: React.MouseEvent) => {
        e.stopPropagation();

        try {
            setError(null);
            const accessToken = await getAccessToken();

            await unarchiveToolApiV1ToolsToolUuidUnarchivePost({
                path: {
                    tool_uuid: toolUuid,
                },
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
            });

            fetchTools();
        } catch (err) {
            setError("Failed to unarchive tool");
            console.error("Error unarchiving tool:", err);
        }
    };

    const [isDragging, setIsDragging] = useState(false);

    const handleJsonInputChange = (text: string) => {
        setImportResult(null);
        if (!text.trim()) {
            setImportParsedTools(null);
            setImportParseError(null);
            return;
        }
        try {
            const parsed = JSON.parse(text);
            const toolsArray = Array.isArray(parsed) ? parsed : [parsed];
            setImportParsedTools(toolsArray);
            setImportParseError(null);
        } catch (err: unknown) {
            setImportParsedTools(null);
            const msg = err instanceof Error ? err.message : "Invalid JSON format";
            setImportParseError("Invalid JSON: " + msg);
        }
    };

    const resetImport = () => {
        setImportParsedTools(null);
        setImportParseError(null);
        setImportResult(null);
        setSelectedFileName(null);
    };

    const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;
        setSelectedFileName(file.name);
        const reader = new FileReader();
        reader.onload = (event) => {
            const content = event.target?.result as string;
            if (content) {
                handleJsonInputChange(content);
            }
        };
        reader.readAsText(file);
    };

    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);
        setImportParseError(null);

        const file = e.dataTransfer.files[0];
        if (file && (file.type === 'application/json' || file.name.endsWith('.json'))) {
            setSelectedFileName(file.name);
            const reader = new FileReader();
            reader.onload = (event) => {
                const content = event.target?.result as string;
                if (content) {
                    handleJsonInputChange(content);
                }
            };
            reader.readAsText(file);
        } else {
            setImportParseError('Please upload a valid JSON file');
        }
    };

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(true);
    };

    const handleDragLeave = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);
    };

    const handleImportTools = async () => {
        if (!importParsedTools || importParsedTools.length === 0) return;
        try {
            setIsImporting(true);
            setImportParseError(null);
            const accessToken = await getAccessToken();
            const response = await importToolsApiV1ToolsImportPost({
                body: { tools: importParsedTools },
                headers: { Authorization: `Bearer ${accessToken}` },
            });

            if (response.error) {
                setImportParseError(detailFromError(response.error, "Failed to import tools"));
                return;
            }

            if (response.data) {
                setImportResult({
                    importedCount: response.data.imported?.length || 0,
                    errors: response.data.errors || [],
                });
                fetchTools();
            }
        } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : "Failed to import tools";
            setImportParseError(msg);
            console.error("Error importing tools:", err);
        } finally {
            setIsImporting(false);
        }
    };

    const handleExportTools = () => {
        if (!tools || tools.length === 0) return;
        const exportData = tools.map((t) => ({
            id: t.id,
            tool_uuid: t.tool_uuid,
            name: t.name,
            description: t.description,
            category: t.category,
            icon: t.icon,
            icon_color: t.icon_color,
            status: t.status,
            definition: t.definition,
            created_at: t.created_at,
        }));

        const jsonStr = JSON.stringify(exportData, null, 2);
        const blob = new Blob([jsonStr], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `dograh-tools-export-${new Date().toISOString().slice(0, 10)}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };

    const filteredTools = tools.filter(
        (tool) =>
            tool.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
            tool.description?.toLowerCase().includes(searchQuery.toLowerCase())
    );

    const activeTools = filteredTools.filter((tool) => tool.status === "active");
    const archivedTools = filteredTools.filter((tool) => tool.status === "archived");

    const getCategoryBadge = (category: string) => {
        switch (category) {
            case "http_api":
                return <Badge variant="default">HTTP API</Badge>;
            case "end_call":
                return <Badge variant="destructive">End Call</Badge>;
            case "calculator":
                return <Badge variant="secondary">Calculator</Badge>;
            case "native":
                return <Badge variant="secondary">Native</Badge>;
            case "integration":
                return <Badge variant="outline">Integration</Badge>;
            case "mcp":
                return <Badge variant="outline">MCP</Badge>;
            default:
                return <Badge variant="outline">{category}</Badge>;
        }
    };

    const getStatusBadge = (status: string) => {
        switch (status) {
            case "active":
                return <Badge className="bg-green-500">Active</Badge>;
            case "draft":
                return <Badge variant="secondary">Draft</Badge>;
            case "archived":
                return <Badge variant="destructive">Archived</Badge>;
            default:
                return <Badge variant="outline">{status}</Badge>;
        }
    };

    if (loading || !user) {
        return (
            <div className="min-h-screen flex items-center justify-center">
                <div className="space-y-4">
                    <Skeleton className="h-12 w-64" />
                    <Skeleton className="h-64 w-96" />
                </div>
            </div>
        );
    }

    return (
        <div className="min-h-screen">
            <div className="container mx-auto px-4 py-8">
                <div className="max-w-6xl mx-auto">
                    <div className="mb-8">
                        <h1 className="text-3xl font-bold mb-2">Tools</h1>
                        <p className="text-muted-foreground">
                            Manage reusable tools that can be used across your workflows.{" "}
                            <a href="https://docs.dograh.com/voice-agent/tools/introduction" target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">
                                Learn more <ExternalLink className="h-3 w-3" />
                            </a>
                        </p>
                    </div>

                    {error && (
                        <div className="mb-4 p-4 bg-destructive/10 border border-destructive/20 rounded-lg text-destructive">
                            {error}
                        </div>
                    )}

                    <Card className="mb-6">
                        <CardHeader>
                            <div className="flex justify-between items-center">
                                <div>
                                    <CardTitle>Your Tools</CardTitle>
                                    <CardDescription>
                                        Create and manage tools for your organization
                                    </CardDescription>
                                </div>
                                <div className="flex items-center gap-2">
                                    <DropdownMenu>
                                        <DropdownMenuTrigger asChild>
                                            <Button variant="outline">
                                                <Upload className="w-4 h-4 mr-2" />
                                                Import / Export
                                                <ChevronDown className="w-4 h-4 ml-2 opacity-70" />
                                            </Button>
                                        </DropdownMenuTrigger>
                                        <DropdownMenuContent align="end">
                                            <DropdownMenuItem onClick={() => setIsImportDialogOpen(true)} className="cursor-pointer">
                                                <Upload className="w-4 h-4 mr-2" />
                                                Import Tools
                                            </DropdownMenuItem>
                                            <DropdownMenuItem onClick={handleExportTools} disabled={tools.length === 0} className="cursor-pointer">
                                                <Download className="w-4 h-4 mr-2" />
                                                Export Tools
                                            </DropdownMenuItem>
                                        </DropdownMenuContent>
                                    </DropdownMenu>
                                    <Button onClick={() => setIsCreateDialogOpen(true)}>
                                        <Plus className="w-4 h-4 mr-2" />
                                        Create Tool
                                    </Button>
                                </div>
                            </div>
                        </CardHeader>
                        <CardContent>
                            {/* Search */}
                            <div className="relative mb-4">
                                <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                                <Input
                                    placeholder="Search tools..."
                                    value={searchQuery}
                                    onChange={(e) => setSearchQuery(e.target.value)}
                                    className="pl-10"
                                />
                            </div>

                            {isLoading ? (
                                <div className="space-y-4">
                                    {[1, 2, 3].map((i) => (
                                        <div
                                            key={i}
                                            className="flex items-center justify-between p-4 border rounded-lg"
                                        >
                                            <div className="space-y-2">
                                                <Skeleton className="h-4 w-32" />
                                                <Skeleton className="h-3 w-48" />
                                            </div>
                                            <Skeleton className="h-8 w-20" />
                                        </div>
                                    ))}
                                </div>
                            ) : activeTools.length === 0 && archivedTools.length === 0 ? (
                                <div className="text-center py-12">
                                    {renderToolIcon("http_api", "w-12 h-12 text-muted-foreground mx-auto mb-4")}
                                    <p className="text-muted-foreground mb-4">
                                        {searchQuery
                                            ? "No tools match your search"
                                            : "No tools found"}
                                    </p>
                                    {!searchQuery && (
                                        <Button onClick={() => setIsCreateDialogOpen(true)}>
                                            Create Your First Tool
                                        </Button>
                                    )}
                                </div>
                            ) : (
                                <>
                                    {/* Active Tools */}
                                    {activeTools.length > 0 ? (
                                        <div className="space-y-4">
                                            {activeTools.map((tool) => (
                                                <div
                                                    key={tool.tool_uuid}
                                                    className="flex items-center justify-between p-4 border rounded-lg hover:bg-muted/50 cursor-pointer transition-colors"
                                                    onClick={() =>
                                                        router.push(`/tools/${tool.tool_uuid}`)
                                                    }
                                                >
                                                    <div className="flex items-center gap-4">
                                                        <div
                                                            className="w-10 h-10 shrink-0 rounded-lg flex items-center justify-center"
                                                            style={{
                                                                backgroundColor:
                                                                    tool.icon_color || getCategoryConfig(tool.category as ToolCategory)?.iconColor || "#3B82F6",
                                                            }}
                                                        >
                                                            {renderToolIcon(tool.category)}
                                                        </div>
                                                        <div>
                                                            <div className="flex items-center gap-2">
                                                                <span className="font-medium">
                                                                    {tool.name}
                                                                </span>
                                                                {getCategoryBadge(tool.category)}
                                                            </div>
                                                            {tool.description && (
                                                                <p className="text-sm text-muted-foreground mt-1">
                                                                    {tool.description}
                                                                </p>
                                                            )}
                                                        </div>
                                                    </div>
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        onClick={(e) =>
                                                            handleDeleteTool(tool.tool_uuid, e)
                                                        }
                                                        className="text-destructive hover:text-destructive/90"
                                                    >
                                                        <Trash2 className="w-4 h-4" />
                                                    </Button>
                                                </div>
                                            ))}
                                        </div>
                                    ) : !searchQuery ? (
                                        <div className="text-center py-8">
                                            <p className="text-muted-foreground mb-4">
                                                No active tools
                                            </p>
                                            <Button onClick={() => setIsCreateDialogOpen(true)}>
                                                Create Your First Tool
                                            </Button>
                                        </div>
                                    ) : null}

                                    {/* Archived Tools */}
                                    {archivedTools.length > 0 && (
                                        <div className="mt-8">
                                            <h3 className="text-lg font-semibold text-muted-foreground mb-4">
                                                Archived Tools
                                            </h3>
                                            <div className="space-y-4">
                                                {archivedTools.map((tool) => (
                                                    <div
                                                        key={tool.tool_uuid}
                                                        className="flex items-center justify-between p-4 border rounded-lg hover:bg-muted/50 cursor-pointer transition-colors opacity-60"
                                                        onClick={() =>
                                                            router.push(`/tools/${tool.tool_uuid}`)
                                                        }
                                                    >
                                                        <div className="flex items-center gap-4">
                                                            <div
                                                                className="w-10 h-10 shrink-0 rounded-lg flex items-center justify-center"
                                                                style={{
                                                                    backgroundColor:
                                                                        tool.icon_color || getCategoryConfig(tool.category as ToolCategory)?.iconColor || "#3B82F6",
                                                                }}
                                                            >
                                                                {renderToolIcon(tool.category)}
                                                            </div>
                                                            <div>
                                                                <div className="flex items-center gap-2">
                                                                    <span className="font-medium">
                                                                        {tool.name}
                                                                    </span>
                                                                    {getCategoryBadge(tool.category)}
                                                                    {getStatusBadge(tool.status)}
                                                                </div>
                                                                {tool.description && (
                                                                    <p className="text-sm text-muted-foreground mt-1">
                                                                        {tool.description}
                                                                    </p>
                                                                )}
                                                            </div>
                                                        </div>
                                                        <Button
                                                            variant="ghost"
                                                            size="sm"
                                                            onClick={(e) =>
                                                                handleUnarchiveTool(tool.tool_uuid, e)
                                                            }
                                                            className="text-primary hover:text-primary/90"
                                                            title="Restore tool"
                                                        >
                                                            <RotateCcw className="w-4 h-4" />
                                                        </Button>
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    )}
                                </>
                            )}
                        </CardContent>
                    </Card>
                </div>
            </div>

            {/* Create Tool Dialog */}
            <Dialog open={isCreateDialogOpen} onOpenChange={(open) => {
                setIsCreateDialogOpen(open);
                if (open) {
                    setCreateError(null);
                } else {
                    // Reset MCP fields when dialog is closed without creating
                    setMcpUrl("");
                    setMcpCredentialUuid("");
                    setMcpToolsFilter("");
                }
            }}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Create New Tool</DialogTitle>
                        <DialogDescription>
                            Create a new tool that can be used in your workflows.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="grid gap-4 py-4">
                        <div className="grid gap-2">
                            <Label>Tool Type</Label>
                            <Select
                                value={newToolCategory}
                                onValueChange={(v) => {
                                    const category = v as ToolCategory;
                                    setNewToolCategory(category);
                                    setCreateError(null);
                                    const categoryConfig = getCategoryConfig(category);
                                    if (categoryConfig?.autoFill) {
                                        setNewToolName(categoryConfig.autoFill.name);
                                        setNewToolDescription(categoryConfig.autoFill.description);
                                    }
                                }}
                            >
                                <SelectTrigger className="w-full">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {TOOL_CATEGORIES.map((category) => (
                                        <SelectItem
                                            key={category.value}
                                            value={category.value}
                                            disabled={category.disabled}
                                        >
                                            {category.label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            <p className="text-xs text-muted-foreground">
                                {getCategoryConfig(newToolCategory)?.description}
                            </p>
                        </div>
                        <div className="grid gap-2">
                            <Label htmlFor="name">Tool Name</Label>
                            <Label className="text-xs text-muted-foreground">
                                Use a descriptive name, like &quot;Get Weather using API&quot; for a tool that fetches weather
                            </Label>
                            <Input
                                id="name"
                                value={newToolName}
                                onChange={(e) => setNewToolName(e.target.value)}
                                placeholder="e.g., Book Appointment, Check Inventory"
                            />
                        </div>
                        <div className="grid gap-2">
                            <Label htmlFor="description">Description (Optional)</Label>
                            <Label className="text-xs text-muted-foreground">
                                Provide a description which makes it easy for LLM to understand what this tool does
                            </Label>
                            <Input
                                id="description"
                                value={newToolDescription}
                                onChange={(e) => setNewToolDescription(e.target.value)}
                                placeholder="What does this tool do?"
                            />
                        </div>

                        {newToolCategory === "mcp" && (
                            <>
                                <div className="grid gap-2">
                                    <Label htmlFor="mcp-url">MCP Server URL</Label>
                                    <Input
                                        id="mcp-url"
                                        value={mcpUrl}
                                        onChange={(e) => setMcpUrl(e.target.value)}
                                        placeholder="https://your-mcp-server.example.com/mcp"
                                    />
                                </div>
                                <div className="grid gap-2">
                                    <Label>Transport</Label>
                                    <Input
                                        value="Streamable HTTP"
                                        disabled
                                        readOnly
                                    />
                                </div>
                                <CredentialSelector
                                    value={mcpCredentialUuid}
                                    onChange={setMcpCredentialUuid}
                                    label="Credential (Optional)"
                                    description="Select a credential for authenticating with the MCP server, or leave empty for no auth."
                                />
                                <div className="grid gap-2">
                                    <Label htmlFor="mcp-tools-filter">Tools Filter (Optional)</Label>
                                    <Input
                                        id="mcp-tools-filter"
                                        value={mcpToolsFilter}
                                        onChange={(e) => setMcpToolsFilter(e.target.value)}
                                        placeholder="e.g., tool_one, tool_two"
                                    />
                                    <p className="text-xs text-muted-foreground">
                                        Comma-separated list of tool names to allow. Leave empty to expose all tools from the server.
                                    </p>
                                </div>
                            </>
                        )}
                    </div>
                    {createError && (
                        <div className="p-3 bg-destructive/10 border border-destructive/20 rounded-lg text-destructive text-sm">
                            {createError}
                        </div>
                    )}
                    <DialogFooter>
                        <Button
                            variant="outline"
                            onClick={() => setIsCreateDialogOpen(false)}
                        >
                            Cancel
                        </Button>
                        <Button onClick={handleCreateTool} disabled={isCreating}>
                            {isCreating ? "Creating..." : "Create Tool"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Import Tools Dialog */}
            <Dialog open={isImportDialogOpen} onOpenChange={(open) => {
                setIsImportDialogOpen(open);
                if (!open) {
                    setImportParsedTools(null);
                    setImportParseError(null);
                    setImportResult(null);
                }
            }}>
                <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Upload className="w-5 h-5 text-primary" />
                            Import Tools from JSON
                        </DialogTitle>
                        <DialogDescription>
                            Upload a JSON file or paste tool definitions to bulk-create tools for your organization. Supports exported arrays or single tool objects.
                        </DialogDescription>
                    </DialogHeader>

                    <div className="grid gap-4 py-3">
                        {!importParsedTools && !importResult ? (
                            /* Drag & Drop Upload Zone (shown when no file is loaded) */
                            <div
                                onDrop={handleDrop}
                                onDragOver={handleDragOver}
                                onDragLeave={handleDragLeave}
                                className={`border-2 border-dashed rounded-lg p-6 text-center transition-colors ${
                                    isDragging
                                        ? 'border-primary bg-primary/5'
                                        : 'border-muted-foreground/25 hover:border-primary/50'
                                }`}
                            >
                                <Upload className="w-8 h-8 mx-auto mb-3 text-muted-foreground" />
                                <p className="text-sm font-medium mb-1">
                                    Drop your tool definition JSON file here
                                </p>
                                <p className="text-xs text-muted-foreground mb-3">
                                    or click to browse from your computer
                                </p>
                                <input
                                    type="file"
                                    accept=".json,application/json"
                                    onChange={handleFileUpload}
                                    className="hidden"
                                    id="tool-file-upload"
                                />
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={() => document.getElementById('tool-file-upload')?.click()}
                                >
                                    Browse Files
                                </Button>
                            </div>
                        ) : null}

                        {/* Syntax Parse Error */}
                        {importParseError && (
                            <div className="p-3 bg-destructive/10 border border-destructive/20 rounded-lg text-destructive text-sm flex items-start gap-2">
                                <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
                                <div>{importParseError}</div>
                            </div>
                        )}

                        {/* Parsed Preview */}
                        {importParsedTools && importParsedTools.length > 0 && !importResult && (
                            <div className="p-4 border rounded-lg bg-muted/30 space-y-3">
                                <div className="flex items-center justify-between">
                                    <div className="flex items-center gap-3">
                                        <div className="p-2 rounded-lg bg-primary/10 text-primary">
                                            <FileJson className="w-6 h-6" />
                                        </div>
                                        <div>
                                            <p className="text-sm font-semibold">{selectedFileName || "Loaded JSON File"}</p>
                                            <p className="text-xs text-muted-foreground">
                                                Ready to import {importParsedTools.length} tool{importParsedTools.length === 1 ? "" : "s"}
                                            </p>
                                        </div>
                                    </div>
                                    <Button variant="ghost" size="sm" onClick={resetImport} className="text-xs text-muted-foreground hover:text-foreground">
                                        Change File
                                    </Button>
                                </div>
                                <div className="max-h-40 overflow-y-auto space-y-1.5 text-xs font-mono pr-1 pt-1 border-t">
                                    {importParsedTools.map((tool, idx) => {
                                        const defObj = typeof tool.definition === "object" && tool.definition ? (tool.definition as Record<string, unknown>) : null;
                                        const name = (tool.name as string) || (defObj?.name as string) || `Tool #${idx + 1}`;
                                        const cat = (tool.category as string) || (defObj?.type as string) || "http_api";
                                        return (
                                            <div key={idx} className="flex items-center justify-between p-2 bg-background rounded border">
                                                <span className="font-medium text-foreground truncate max-w-[300px]">{name}</span>
                                                <Badge variant="outline" className="text-[10px] px-1.5 py-0">{cat}</Badge>
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                        )}

                        {/* Import Result Summary */}
                        {importResult && (
                            <div className="space-y-3">
                                {importResult.importedCount > 0 && (
                                    <div className="p-4 bg-green-500/10 border border-green-500/20 text-green-700 dark:text-green-400 rounded-lg text-sm flex items-center justify-between font-medium">
                                        <div className="flex items-center gap-2">
                                            <CheckCircle2 className="w-5 h-5 shrink-0" />
                                            Successfully imported {importResult.importedCount} tool{importResult.importedCount === 1 ? "" : "s"}!
                                        </div>
                                        <Button variant="outline" size="sm" onClick={resetImport} className="text-xs h-7 px-2">
                                            Import Another File
                                        </Button>
                                    </div>
                                )}
                                {importResult.errors.length > 0 && (
                                    <div className="p-3 bg-destructive/10 border border-destructive/20 rounded-lg text-sm space-y-2">
                                        <div className="font-semibold text-destructive flex items-center gap-1.5">
                                            <AlertCircle className="w-4 h-4" />
                                            {importResult.errors.length} tool{importResult.errors.length === 1 ? "" : "s"} failed to import:
                                        </div>
                                        <div className="space-y-1 max-h-32 overflow-y-auto text-xs text-destructive/90">
                                            {importResult.errors.map((err, idx) => (
                                                <div key={idx} className="border-b border-destructive/10 pb-1">
                                                    <span className="font-mono font-bold">#{err.index + 1} ({err.name}):</span> {err.error}
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}
                    </div>

                    <DialogFooter>
                        <Button
                            variant="outline"
                            onClick={() => setIsImportDialogOpen(false)}
                        >
                            {importResult ? "Close" : "Cancel"}
                        </Button>
                        {!importResult && (
                            <Button
                                onClick={handleImportTools}
                                disabled={isImporting || !importParsedTools || importParsedTools.length === 0}
                            >
                                {isImporting ? "Importing..." : `Import ${importParsedTools?.length ? importParsedTools.length : ""} Tool${importParsedTools?.length === 1 ? "" : "s"}`}
                            </Button>
                        )}
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
