"use client";

import { ArrowLeft, Code, Loader2, Save } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import {
    getToolApiV1ToolsToolUuidGet,
    updateToolApiV1ToolsToolUuidPut,
} from "@/client/sdk.gen";
import type { ToolResponse, TransferCallConfig as APITransferCallConfig } from "@/client/types.gen";
import { type HttpMethod, type KeyValueItem, type ToolParameter, validateUrl } from "@/components/http";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";

import {
    type EndCallConfig,
    type EndCallMessageType,
    getCategoryConfig,
    getToolTypeLabel,
    renderToolIcon,
    type ToolCategory,
} from "../config";
import { EndCallToolConfig, HttpApiToolConfig, TransferCallToolConfig } from "./components";

// Extended HttpApiConfig with parameters (until client types are regenerated)
interface HttpApiConfigWithParams {
    method?: string;
    url?: string;
    headers?: Record<string, string>;
    credential_uuid?: string;
    parameters?: ToolParameter[];
    timeout_ms?: number;
}

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

    // Common form state
    const [name, setName] = useState("");
    const [description, setDescription] = useState("");

    // HTTP API form state
    const [httpMethod, setHttpMethod] = useState<HttpMethod>("POST");
    const [url, setUrl] = useState("");
    const [credentialUuid, setCredentialUuid] = useState("");
    const [headers, setHeaders] = useState<KeyValueItem[]>([]);
    const [parameters, setParameters] = useState<ToolParameter[]>([]);
    const [timeoutMs, setTimeoutMs] = useState(5000);

    // End Call form state
    const [endCallMessageType, setEndCallMessageType] = useState<EndCallMessageType>("none");
    const [endCallCustomMessage, setEndCallCustomMessage] = useState("");

    // Transfer Call form state
    const [transferDestination, setTransferDestination] = useState("");
    const [transferMessageType, setTransferMessageType] = useState<EndCallMessageType>("none");
    const [transferCustomMessage, setTransferCustomMessage] = useState("");
    const [transferTimeout, setTransferTimeout] = useState(30);

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

        if (tool.category === "end_call") {
            // Populate end call specific fields
            const config = tool.definition?.config as EndCallConfig | undefined;
            if (config) {
                setEndCallMessageType(config.messageType || "none");
                setEndCallCustomMessage(config.customMessage || "");
            } else {
                setEndCallMessageType("none");
                setEndCallCustomMessage("");
            }
        } else if (tool.category === "transfer_call") {
            // Populate transfer call specific fields
            const config = tool.definition?.config as APITransferCallConfig | undefined;
            if (config) {
                setTransferDestination(config.destination || "");
                setTransferMessageType(config.messageType || "none");
                setTransferCustomMessage(config.customMessage || "");
                setTransferTimeout(config.timeout ?? 30);
            } else {
                setTransferDestination("");
                setTransferMessageType("none");
                setTransferCustomMessage("");
                setTransferTimeout(30);
            }
        } else {
            // Populate HTTP API specific fields
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
        }
    };

    useEffect(() => {
        fetchTool();
    }, [fetchTool]);

    const handleSave = async () => {
        if (!tool) return;

        // Validation based on tool type
        if (tool.category === "transfer_call") {
            // Validate destination phone number for Transfer Call tools
            const e164Pattern = /^\+[1-9]\d{1,14}$/;
            if (!transferDestination || !e164Pattern.test(transferDestination)) {
                setError("Please enter a valid phone number in E.164 format (e.g., +1234567890)");
                return;
            }
        } else if (tool.category !== "end_call") {
            // Validate URL for HTTP API tools
            const urlValidation = validateUrl(url);
            if (!urlValidation.valid) {
                setError(urlValidation.error || "Invalid URL");
                return;
            }

            // Validate parameters have names
            const invalidParams = parameters.filter((p) => !p.name.trim());
            if (invalidParams.length > 0) {
                setError("All parameters must have a name");
                return;
            }
        }

        try {
            setIsSaving(true);
            setError(null);
            setSaveSuccess(false);
            const accessToken = await getAccessToken();

            let requestBody;

            if (tool.category === "end_call") {
                // Build end call request body
                requestBody = {
                    name,
                    description: description || undefined,
                    definition: {
                        schema_version: 1,
                        type: "end_call",
                        config: {
                            messageType: endCallMessageType,
                            customMessage: endCallMessageType === "custom" ? endCallCustomMessage : undefined,
                        },
                    },
                };
            } else if (tool.category === "transfer_call") {
                // Build transfer call request body
                requestBody = {
                    name,
                    description: description || undefined,
                    definition: {
                        schema_version: 1,
                        type: "transfer_call",
                        config: {
                            destination: transferDestination,
                            messageType: transferMessageType,
                            customMessage: transferMessageType === "custom" ? transferCustomMessage : undefined,
                            timeout: transferTimeout,
                        },
                    },
                };
            } else {
                // Build HTTP API request body
                const headersObject: Record<string, string> = {};
                headers.filter((h) => h.key && h.value).forEach((h) => {
                    headersObject[h.key] = h.value;
                });

                const validParameters = parameters.filter((p) => p.name.trim());

                requestBody = {
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
            }

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

    const isEndCallTool = tool.category === "end_call";
    const isTransferCallTool = tool.category === "transfer_call";
    const categoryConfig = getCategoryConfig(tool.category as ToolCategory);

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
                                        backgroundColor: tool.icon_color || categoryConfig?.iconColor || "#3B82F6",
                                    }}
                                >
                                    {renderToolIcon(tool.category)}
                                </div>
                                <div>
                                    <h1 className="text-xl font-bold">{name}</h1>
                                    <p className="text-sm text-muted-foreground">
                                        {getToolTypeLabel(tool.category)}
                                    </p>
                                </div>
                            </div>
                        </div>
                        <div className="flex items-center gap-2">
                            {!isEndCallTool && !isTransferCallTool && (
                                <Button
                                    variant="outline"
                                    onClick={() => setShowCodeDialog(true)}
                                >
                                    <Code className="w-4 h-4 mr-2" />
                                    View Code
                                </Button>
                            )}
                        </div>
                    </div>

                    {isEndCallTool ? (
                        <EndCallToolConfig
                            name={name}
                            onNameChange={setName}
                            description={description}
                            onDescriptionChange={setDescription}
                            messageType={endCallMessageType}
                            onMessageTypeChange={setEndCallMessageType}
                            customMessage={endCallCustomMessage}
                            onCustomMessageChange={setEndCallCustomMessage}
                        />
                    ) : isTransferCallTool ? (
                        <TransferCallToolConfig
                            name={name}
                            onNameChange={setName}
                            description={description}
                            onDescriptionChange={setDescription}
                            destination={transferDestination}
                            onDestinationChange={setTransferDestination}
                            messageType={transferMessageType}
                            onMessageTypeChange={setTransferMessageType}
                            customMessage={transferCustomMessage}
                            onCustomMessageChange={setTransferCustomMessage}
                            timeout={transferTimeout}
                            onTimeoutChange={setTransferTimeout}
                        />
                    ) : (
                        <HttpApiToolConfig
                            name={name}
                            onNameChange={setName}
                            description={description}
                            onDescriptionChange={setDescription}
                            httpMethod={httpMethod}
                            onHttpMethodChange={setHttpMethod}
                            url={url}
                            onUrlChange={setUrl}
                            credentialUuid={credentialUuid}
                            onCredentialUuidChange={setCredentialUuid}
                            headers={headers}
                            onHeadersChange={setHeaders}
                            parameters={parameters}
                            onParametersChange={setParameters}
                            timeoutMs={timeoutMs}
                            onTimeoutMsChange={setTimeoutMs}
                        />
                    )}

                    {error && (
                        <div className="mt-4 p-4 bg-destructive/10 border border-destructive/20 rounded-lg text-destructive">
                            {error}
                        </div>
                    )}

                    {saveSuccess && (
                        <div className="mt-4 p-4 bg-green-500/10 border border-green-500/20 rounded-lg text-green-600">
                            Tool saved successfully!
                        </div>
                    )}

                    <div className="flex justify-end mt-6">
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
            </div>

            {/* Code View Dialog (only for HTTP API tools) */}
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
