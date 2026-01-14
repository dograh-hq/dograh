"use client";

import { Plus, RotateCcw, Search, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import {
    createToolApiV1ToolsPost,
    deleteToolApiV1ToolsToolUuidDelete,
    listToolsApiV1ToolsGet,
    unarchiveToolApiV1ToolsToolUuidUnarchivePost,
} from "@/client/sdk.gen";
import type { ToolResponse } from "@/client/types.gen";
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
import { useAuth } from "@/lib/auth";

import {
    createToolDefinition,
    getCategoryConfig,
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

        try {
            setIsCreating(true);
            setCreateError(null);
            const accessToken = await getAccessToken();

            const categoryConfig = getCategoryConfig(newToolCategory);
            const response = await createToolApiV1ToolsPost({
                body: {
                    name: newToolName,
                    description: newToolDescription || undefined,
                    category: newToolCategory,
                    icon: categoryConfig?.iconName || "globe",
                    icon_color: categoryConfig?.iconColor || "#3B82F6",
                    definition: createToolDefinition(newToolCategory),
                },
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
            });

            if (response.error) {
                const errorDetail = (response.error as { detail?: string })?.detail;
                setCreateError(errorDetail || "Failed to create tool");
                return;
            }

            if (response.data) {
                setIsCreateDialogOpen(false);
                setNewToolName("");
                setNewToolDescription("");
                setNewToolCategory("http_api");
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
            case "native":
                return <Badge variant="secondary">Native</Badge>;
            case "integration":
                return <Badge variant="outline">Integration</Badge>;
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
            <div className="min-h-screen bg-background flex items-center justify-center">
                <div className="space-y-4">
                    <Skeleton className="h-12 w-64" />
                    <Skeleton className="h-64 w-96" />
                </div>
            </div>
        );
    }

    return (
        <div className="min-h-screen bg-background">
            <div className="container mx-auto px-4 py-8">
                <div className="max-w-6xl mx-auto">
                    <div className="mb-8">
                        <h1 className="text-3xl font-bold mb-2">Tools</h1>
                        <p className="text-muted-foreground">
                            Manage reusable tools that can be used across your workflows
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
                                <Button onClick={() => setIsCreateDialogOpen(true)}>
                                    <Plus className="w-4 h-4 mr-2" />
                                    Create Tool
                                </Button>
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
                                                            className="w-10 h-10 rounded-lg flex items-center justify-center"
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
                                                                className="w-10 h-10 rounded-lg flex items-center justify-center"
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
                if (open) setCreateError(null);
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
        </div>
    );
}
