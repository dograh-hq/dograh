"use client";

import { ArrowLeft, Code, Globe, Loader2, Save } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import {
    getToolApiV1ToolsToolUuidGet,
    updateToolApiV1ToolsToolUuidPut,
} from "@/client/sdk.gen";
import type { ToolResponse } from "@/client/types.gen";

// Extended HttpApiConfig with parameters (until client types are regenerated)
interface HttpApiConfigWithParams {
    method?: string;
    url?: string;
    headers?: Record<string, string>;
    credential_uuid?: string;
    parameters?: ToolParameter[];
    timeout_ms?: number;
}
import {
    CredentialSelector,
    type HttpMethod,
    HttpMethodSelector,
    KeyValueEditor,
    type KeyValueItem,
    ParameterEditor,
    type ToolParameter,
} from "@/components/http";
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
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/lib/auth";

export default function ToolDetailPage() {
    const { toolUuid } = useParams<{ toolUuid: string }>();
    const { user, getAccessToken, redirectToLogin, loading } = useAuth();
    const router = useRouter();

    const [tool, setTool] = useState<ToolResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [isSaving, setIsSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [saveSuccess, setSaveSuccess] = useState(false);
    const [showCodeDialog, setShowCodeDialog] = useState(false);

    // Form state
    const [name, setName] = useState("");
    const [description, setDescription] = useState("");
    const [httpMethod, setHttpMethod] = useState<HttpMethod>("POST");
    const [url, setUrl] = useState("");
    const [credentialUuid, setCredentialUuid] = useState("");
    const [headers, setHeaders] = useState<KeyValueItem[]>([]);
    const [parameters, setParameters] = useState<ToolParameter[]>([]);
    const [timeoutMs, setTimeoutMs] = useState(5000);

    // Redirect if not authenticated
    useEffect(() => {
        if (!loading && !user) {
            redirectToLogin();
        }
    }, [loading, user, redirectToLogin]);

    const fetchTool = useCallback(async () => {
        if (loading || !user || !toolUuid) return;

        try {
            setIsLoading(true);
            setError(null);
            const accessToken = await getAccessToken();

            const response = await getToolApiV1ToolsToolUuidGet({
                path: { tool_uuid: toolUuid },
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
            });

            if (response.data) {
                setTool(response.data);
                populateFormFromTool(response.data);
            }
        } catch (err) {
            setError("Failed to fetch tool");
            console.error("Error fetching tool:", err);
        } finally {
            setIsLoading(false);
        }
    }, [loading, user, toolUuid, getAccessToken]);

    const populateFormFromTool = (tool: ToolResponse) => {
        setName(tool.name);
        setDescription(tool.description || "");

        const config = tool.definition?.config as HttpApiConfigWithParams | undefined;
        if (config) {
            setHttpMethod((config.method as HttpMethod) || "POST");
            setUrl(config.url || "");
            setCredentialUuid(config.credential_uuid || "");
            setTimeoutMs(config.timeout_ms || 5000);

            // Convert headers object to array
            if (config.headers) {
                setHeaders(
                    Object.entries(config.headers).map(([key, value]) => ({
                        key,
                        value: value as string,
                    }))
                );
            } else {
                setHeaders([]);
            }

            // Load parameters
            if (config.parameters && Array.isArray(config.parameters)) {
                setParameters(
                    config.parameters.map((p: ToolParameter) => ({
                        name: p.name || "",
                        type: p.type || "string",
                        description: p.description || "",
                        required: p.required ?? true,
                    }))
                );
            } else {
                setParameters([]);
            }
        }
    };

    useEffect(() => {
        fetchTool();
    }, [fetchTool]);

    const handleSave = async () => {
        // Validate URL
        if (!url.trim()) {
            setError("URL is required");
            return;
        }

        // Validate parameters have names
        const invalidParams = parameters.filter((p) => !p.name.trim());
        if (invalidParams.length > 0) {
            setError("All parameters must have a name");
            return;
        }

        try {
            setIsSaving(true);
            setError(null);
            setSaveSuccess(false);
            const accessToken = await getAccessToken();

            // Convert headers array to object
            const headersObject: Record<string, string> = {};
            headers.filter((h) => h.key && h.value).forEach((h) => {
                headersObject[h.key] = h.value;
            });

            // Filter out empty parameters
            const validParameters = parameters.filter((p) => p.name.trim());

            // Build the request body (cast needed until client types are regenerated)
            const requestBody = {
                name,
                description: description || undefined,
                definition: {
                    schema_version: 1,
                    type: "http_api",
                    config: {
                        method: httpMethod,
                        url,
                        credential_uuid: credentialUuid || undefined,
                        headers:
                            Object.keys(headersObject).length > 0
                                ? headersObject
                                : undefined,
                        parameters:
                            validParameters.length > 0 ? validParameters : undefined,
                        timeout_ms: timeoutMs,
                    },
                },
            };

            const response = await updateToolApiV1ToolsToolUuidPut({
                path: { tool_uuid: toolUuid },
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                body: requestBody as any,
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
            });

            if (response.data) {
                setTool(response.data);
                setSaveSuccess(true);
                setTimeout(() => setSaveSuccess(false), 3000);
            }
        } catch (err) {
            setError("Failed to save tool");
            console.error("Error saving tool:", err);
        } finally {
            setIsSaving(false);
        }
    };

    const getCodeSnippet = () => {
        if (!tool) return "";

        const headersObj: Record<string, string> = {
            "Content-Type": "application/json",
        };
        headers.filter((h) => h.key && h.value).forEach((h) => {
            headersObj[h.key] = h.value;
        });

        // Build example body from parameters
        const exampleBody: Record<string, unknown> = {};
        parameters.forEach((p) => {
            if (p.type === "number") {
                exampleBody[p.name] = 0;
            } else if (p.type === "boolean") {
                exampleBody[p.name] = true;
            } else {
                exampleBody[p.name] = `<${p.name}>`;
            }
        });

        const hasBody = httpMethod !== "GET" && httpMethod !== "DELETE" && parameters.length > 0;

        return `// ${tool.name}
// ${tool.description || "HTTP API Tool"}

const response = await fetch("${url}", {
    method: "${httpMethod}",
    headers: ${JSON.stringify(headersObj, null, 4)},${hasBody ? `
    body: JSON.stringify(${JSON.stringify(exampleBody, null, 4)}),` : ""}
});

const data = await response.json();`;
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

    if (isLoading) {
        return (
            <div className="min-h-screen bg-background">
                <div className="container mx-auto px-4 py-8">
                    <div className="max-w-4xl mx-auto space-y-6">
                        <Skeleton className="h-8 w-48" />
                        <Skeleton className="h-64 w-full" />
                    </div>
                </div>
            </div>
        );
    }

    if (!tool) {
        return (
            <div className="min-h-screen bg-background">
                <div className="container mx-auto px-4 py-8">
                    <div className="max-w-4xl mx-auto text-center">
                        <h1 className="text-2xl font-bold mb-4">Tool not found</h1>
                        <Button onClick={() => router.push("/tools")}>
                            <ArrowLeft className="w-4 h-4 mr-2" />
                            Back to Tools
                        </Button>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="min-h-screen bg-background">
            <div className="container mx-auto px-4 py-8">
                <div className="max-w-4xl mx-auto">
                    {/* Header */}
                    <div className="flex items-center justify-between mb-6">
                        <div className="flex items-center gap-4">
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => router.push("/tools")}
                            >
                                <ArrowLeft className="w-4 h-4 mr-2" />
                                Back
                            </Button>
                            <div className="flex items-center gap-3">
                                <div
                                    className="w-10 h-10 rounded-lg flex items-center justify-center"
                                    style={{
                                        backgroundColor: tool.icon_color || "#3B82F6",
                                    }}
                                >
                                    <Globe className="w-5 h-5 text-white" />
                                </div>
                                <div>
                                    <h1 className="text-xl font-bold">{name}</h1>
                                    <p className="text-sm text-muted-foreground">
                                        HTTP API Tool
                                    </p>
                                </div>
                            </div>
                        </div>
                        <div className="flex items-center gap-2">
                            <Button
                                variant="outline"
                                onClick={() => setShowCodeDialog(true)}
                            >
                                <Code className="w-4 h-4 mr-2" />
                                View Code
                            </Button>
                            <Button onClick={handleSave} disabled={isSaving}>
                                {isSaving ? (
                                    <>
                                        <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                                        Saving...
                                    </>
                                ) : (
                                    <>
                                        <Save className="w-4 h-4 mr-2" />
                                        Save
                                    </>
                                )}
                            </Button>
                        </div>
                    </div>

                    {error && (
                        <div className="mb-4 p-4 bg-destructive/10 border border-destructive/20 rounded-lg text-destructive">
                            {error}
                        </div>
                    )}

                    {saveSuccess && (
                        <div className="mb-4 p-4 bg-green-500/10 border border-green-500/20 rounded-lg text-green-600">
                            Tool saved successfully!
                        </div>
                    )}

                    <Card>
                        <CardHeader>
                            <CardTitle>Tool Configuration</CardTitle>
                            <CardDescription>
                                Configure the HTTP API endpoint and request settings
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <Tabs defaultValue="settings" className="w-full">
                                <TabsList className="grid w-full grid-cols-3">
                                    <TabsTrigger value="settings">Settings</TabsTrigger>
                                    <TabsTrigger value="auth">Authentication</TabsTrigger>
                                    <TabsTrigger value="parameters">Parameters</TabsTrigger>
                                </TabsList>

                                <TabsContent value="settings" className="space-y-4 mt-4">
                                    <div className="grid gap-2">
                                        <Label>Tool Name</Label>
                                        <Label className="text-xs text-muted-foreground">
                                            Use a descriptive name, like &quot;Get Weather using API&quot; for a tool that fetches weather
                                        </Label>
                                        <Input
                                            value={name}
                                            onChange={(e) => setName(e.target.value)}
                                            placeholder="e.g., Book Appointment"
                                        />
                                    </div>

                                    <div className="grid gap-2">
                                        <Label>Description</Label>
                                        <Label className="text-xs text-muted-foreground">
                                            Provide a description which makes it easy for LLM to understand what this tool does
                                        </Label>
                                        <Textarea
                                            value={description}
                                            onChange={(e) => setDescription(e.target.value)}
                                            placeholder="What does this tool do?"
                                            rows={3}
                                        />
                                    </div>

                                    <div className="grid grid-cols-2 gap-4">
                                        <div className="grid gap-2">
                                            <Label>HTTP Method</Label>
                                            <HttpMethodSelector
                                                value={httpMethod}
                                                onChange={setHttpMethod}
                                            />
                                        </div>
                                        <div className="grid gap-2">
                                            <Label>Timeout (ms)</Label>
                                            <Input
                                                type="number"
                                                value={timeoutMs}
                                                onChange={(e) =>
                                                    setTimeoutMs(parseInt(e.target.value) || 5000)
                                                }
                                                min={1000}
                                                max={30000}
                                            />
                                        </div>
                                    </div>

                                    <div className="grid gap-2">
                                        <Label>Endpoint URL</Label>
                                        <Input
                                            value={url}
                                            onChange={(e) => setUrl(e.target.value)}
                                            placeholder="https://api.example.com/appointments"
                                        />
                                    </div>
                                </TabsContent>

                                <TabsContent value="auth" className="space-y-4 mt-4">
                                    <CredentialSelector
                                        value={credentialUuid}
                                        onChange={setCredentialUuid}
                                    />
                                </TabsContent>

                                <TabsContent value="parameters" className="space-y-4 mt-4">
                                    <div className="grid gap-2">
                                        <Label>Tool Parameters</Label>
                                        <Label className="text-xs text-muted-foreground">
                                            Define the parameters that the LLM will provide when calling this tool.
                                            These will be sent as JSON body for POST/PUT/PATCH or as URL query params for GET/DELETE.
                                        </Label>
                                        <ParameterEditor
                                            parameters={parameters}
                                            onChange={setParameters}
                                        />
                                    </div>

                                    <div className="grid gap-2 pt-4 border-t">
                                        <Label>Custom Headers</Label>
                                        <Label className="text-xs text-muted-foreground">
                                            Add custom headers to include in the request (optional)
                                        </Label>
                                        <KeyValueEditor
                                            items={headers}
                                            onChange={setHeaders}
                                            keyPlaceholder="Header name"
                                            valuePlaceholder="Header value"
                                            addButtonText="Add Header"
                                        />
                                    </div>
                                </TabsContent>
                            </Tabs>
                        </CardContent>
                    </Card>
                </div>
            </div>

            {/* Code View Dialog */}
            <Dialog open={showCodeDialog} onOpenChange={setShowCodeDialog}>
                <DialogContent className="max-w-2xl">
                    <DialogHeader>
                        <DialogTitle>Code Preview</DialogTitle>
                        <DialogDescription>
                            JavaScript code to make this API call
                        </DialogDescription>
                    </DialogHeader>
                    <div className="bg-muted rounded-lg p-4 font-mono text-sm overflow-auto max-h-96">
                        <pre>{getCodeSnippet()}</pre>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}
