import { NodeProps, NodeToolbar, Position } from "@xyflow/react";
import { AlertCircle, Circle, Edit, Link2, Loader2, PlusIcon, Trash2Icon } from "lucide-react";
import { memo, useCallback, useEffect, useState } from "react";

import { useWorkflow } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import {
    createCredentialApiV1CredentialsPost,
    listCredentialsApiV1CredentialsGet,
} from "@/client";
import { CredentialResponse, WebhookCredentialType } from "@/client/types.gen";
import { FlowNodeData } from "@/components/flow/types";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { JsonEditor, validateJson } from "@/components/ui/json-editor";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/lib/auth";

import { NodeContent } from "./common/NodeContent";
import { NodeEditDialog } from "./common/NodeEditDialog";
import { useNodeHandlers } from "./common/useNodeHandlers";

interface WebhookNodeProps extends NodeProps {
    data: FlowNodeData;
}

type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

interface CustomHeader {
    key: string;
    value: string;
}

export const WebhookNode = memo(({ data, selected, id }: WebhookNodeProps) => {
    const { open, setOpen, handleSaveNodeData, handleDeleteNode } = useNodeHandlers({ id });
    const { saveWorkflow } = useWorkflow();
    const { getAccessToken } = useAuth();

    // Form state
    const [name, setName] = useState(data.name || "Webhook");
    const [enabled, setEnabled] = useState(data.enabled ?? true);
    const [httpMethod, setHttpMethod] = useState<HttpMethod>(data.http_method || "POST");
    const [endpointUrl, setEndpointUrl] = useState(data.endpoint_url || "");
    const [credentialUuid, setCredentialUuid] = useState(data.credential_uuid || "");
    const [customHeaders, setCustomHeaders] = useState<CustomHeader[]>(
        data.custom_headers || []
    );
    const [payloadTemplate, setPayloadTemplate] = useState(
        data.payload_template ? JSON.stringify(data.payload_template, null, 2) : "{}"
    );

    // Credentials state
    const [credentials, setCredentials] = useState<CredentialResponse[]>([]);
    const [credentialsLoading, setCredentialsLoading] = useState(false);

    // Fetch credentials when dialog opens
    const fetchCredentials = useCallback(async () => {
        setCredentialsLoading(true);
        try {
            const accessToken = await getAccessToken();
            const response = await listCredentialsApiV1CredentialsGet({
                headers: { Authorization: `Bearer ${accessToken}` },
            });
            if (response.error) {
                console.error("Failed to fetch credentials:", response.error);
                setCredentials([]);
                return;
            }
            if (response.data) {
                setCredentials(response.data);
            }
        } catch (error) {
            console.error("Failed to fetch credentials:", error);
            setCredentials([]);
        } finally {
            setCredentialsLoading(false);
        }
    }, [getAccessToken]);

    // Validation state - only shown on save attempt
    const [jsonError, setJsonError] = useState<string | null>(null);
    const [endpointError, setEndpointError] = useState<string | null>(null);

    const handleSave = async () => {
        // Validate endpoint URL
        if (!endpointUrl.trim()) {
            setEndpointError('Endpoint URL is required');
            return;
        }
        setEndpointError(null);

        // Validate JSON payload
        const validation = validateJson(payloadTemplate);
        if (!validation.valid) {
            setJsonError(validation.error || 'Invalid JSON. Please fix the payload template before saving.');
            return;
        }
        setJsonError(null);

        handleSaveNodeData({
            ...data,
            name,
            enabled,
            http_method: httpMethod,
            endpoint_url: endpointUrl,
            credential_uuid: credentialUuid || undefined,
            custom_headers: customHeaders.filter((h) => h.key && h.value),
            payload_template: validation.parsed as Record<string, unknown>,
        });
        setOpen(false);
        setTimeout(async () => {
            await saveWorkflow();
        }, 100);
    };

    const handleOpenChange = (newOpen: boolean) => {
        if (newOpen) {
            setName(data.name || "Webhook");
            setEnabled(data.enabled ?? true);
            setHttpMethod(data.http_method || "POST");
            setEndpointUrl(data.endpoint_url || "");
            setCredentialUuid(data.credential_uuid || "");
            setCustomHeaders(data.custom_headers || []);
            setPayloadTemplate(
                data.payload_template ? JSON.stringify(data.payload_template, null, 2) : "{}"
            );
            // Clear any previous errors
            setJsonError(null);
            setEndpointError(null);
            // Fetch credentials when dialog opens
            fetchCredentials();
        }
        setOpen(newOpen);
    };

    useEffect(() => {
        if (open) {
            setName(data.name || "Webhook");
            setEnabled(data.enabled ?? true);
            setHttpMethod(data.http_method || "POST");
            setEndpointUrl(data.endpoint_url || "");
            setCredentialUuid(data.credential_uuid || "");
            setCustomHeaders(data.custom_headers || []);
            setPayloadTemplate(
                data.payload_template ? JSON.stringify(data.payload_template, null, 2) : "{}"
            );
        }
    }, [data, open]);

    const truncateUrl = (url: string, maxLength: number = 30) => {
        if (!url) return "Not configured";
        if (url.length <= maxLength) return url;
        return url.substring(0, maxLength) + "...";
    };

    return (
        <>
            <NodeContent
                selected={selected}
                invalid={data.invalid}
                selected_through_edge={data.selected_through_edge}
                hovered_through_edge={data.hovered_through_edge}
                title={data.name || "Webhook"}
                icon={<Link2 />}
                nodeType="webhook"
                onDoubleClick={() => handleOpenChange(true)}
                nodeId={id}
            >
                <div className="space-y-2">
                    <div className="flex items-center gap-2">
                        <span className="text-xs font-mono bg-muted px-1.5 py-0.5 rounded">
                            {data.http_method || "POST"}
                        </span>
                        <span className="text-xs text-muted-foreground truncate flex-1">
                            {truncateUrl(data.endpoint_url || "")}
                        </span>
                    </div>
                    <div className="flex items-center gap-1.5">
                        <Circle
                            className={`h-2 w-2 ${data.enabled !== false ? "fill-green-500 text-green-500" : "fill-gray-400 text-gray-400"}`}
                        />
                        <span className="text-xs text-muted-foreground">
                            {data.enabled !== false ? "Enabled" : "Disabled"}
                        </span>
                    </div>
                </div>
            </NodeContent>

            <NodeToolbar isVisible={selected} position={Position.Right}>
                <div className="flex flex-col gap-1">
                    <Button onClick={() => handleOpenChange(true)} variant="outline" size="icon">
                        <Edit />
                    </Button>
                    <Button onClick={handleDeleteNode} variant="outline" size="icon">
                        <Trash2Icon />
                    </Button>
                </div>
            </NodeToolbar>

            <NodeEditDialog
                open={open}
                onOpenChange={handleOpenChange}
                nodeData={data}
                title="Edit Webhook"
                onSave={handleSave}
                error={endpointError || jsonError}
            >
                {open && (
                    <WebhookNodeEditForm
                        name={name}
                        setName={setName}
                        enabled={enabled}
                        setEnabled={setEnabled}
                        httpMethod={httpMethod}
                        setHttpMethod={setHttpMethod}
                        endpointUrl={endpointUrl}
                        setEndpointUrl={setEndpointUrl}
                        credentialUuid={credentialUuid}
                        setCredentialUuid={setCredentialUuid}
                        credentials={credentials}
                        credentialsLoading={credentialsLoading}
                        onRefreshCredentials={fetchCredentials}
                        getAccessToken={getAccessToken}
                        customHeaders={customHeaders}
                        setCustomHeaders={setCustomHeaders}
                        payloadTemplate={payloadTemplate}
                        setPayloadTemplate={setPayloadTemplate}
                    />
                )}
            </NodeEditDialog>
        </>
    );
});

interface WebhookNodeEditFormProps {
    name: string;
    setName: (value: string) => void;
    enabled: boolean;
    setEnabled: (value: boolean) => void;
    httpMethod: HttpMethod;
    setHttpMethod: (value: HttpMethod) => void;
    endpointUrl: string;
    setEndpointUrl: (value: string) => void;
    credentialUuid: string;
    setCredentialUuid: (value: string) => void;
    credentials: CredentialResponse[];
    credentialsLoading: boolean;
    onRefreshCredentials: () => Promise<void>;
    getAccessToken: () => Promise<string>;
    customHeaders: CustomHeader[];
    setCustomHeaders: (value: CustomHeader[]) => void;
    payloadTemplate: string;
    setPayloadTemplate: (value: string) => void;
}

const WebhookNodeEditForm = ({
    name,
    setName,
    enabled,
    setEnabled,
    httpMethod,
    setHttpMethod,
    endpointUrl,
    setEndpointUrl,
    credentialUuid,
    setCredentialUuid,
    credentials,
    credentialsLoading,
    onRefreshCredentials,
    getAccessToken,
    customHeaders,
    setCustomHeaders,
    payloadTemplate,
    setPayloadTemplate,
}: WebhookNodeEditFormProps) => {
    // Add Credential Dialog state
    const [isAddCredentialOpen, setIsAddCredentialOpen] = useState(false);
    const [newCredName, setNewCredName] = useState("");
    const [newCredDescription, setNewCredDescription] = useState("");
    const [newCredType, setNewCredType] = useState<WebhookCredentialType>("bearer_token");
    const [newCredData, setNewCredData] = useState<Record<string, string>>({});
    const [isCreatingCredential, setIsCreatingCredential] = useState(false);
    const [credentialError, setCredentialError] = useState<string | null>(null);

    const handleCreateCredential = async () => {
        if (!newCredName.trim()) return;

        setIsCreatingCredential(true);
        setCredentialError(null);
        try {
            const accessToken = await getAccessToken();
            const response = await createCredentialApiV1CredentialsPost({
                headers: { Authorization: `Bearer ${accessToken}` },
                body: {
                    name: newCredName,
                    description: newCredDescription || undefined,
                    credential_type: newCredType,
                    credential_data: newCredData,
                },
            });

            if (response.error) {
                const errorDetail = (response.error as { detail?: string })?.detail
                    || "Failed to create credential";
                setCredentialError(errorDetail);
                return;
            }

            if (response.data) {
                // Refresh credentials list
                await onRefreshCredentials();
                // Select the newly created credential
                setCredentialUuid(response.data.uuid);
                // Close dialog and reset form
                setIsAddCredentialOpen(false);
                setNewCredName("");
                setNewCredDescription("");
                setNewCredType("bearer_token");
                setNewCredData({});
                setCredentialError(null);
            }
        } catch (error) {
            console.error("Failed to create credential:", error);
            setCredentialError(
                error instanceof Error ? error.message : "An unexpected error occurred"
            );
        } finally {
            setIsCreatingCredential(false);
        }
    };

    const handleAddCredentialDialogChange = (open: boolean) => {
        setIsAddCredentialOpen(open);
        if (!open) {
            // Reset error when closing dialog
            setCredentialError(null);
        }
    };

    const getCredentialDataFields = (type: WebhookCredentialType) => {
        switch (type) {
            case "api_key":
                return [
                    { key: "header_name", label: "Header Name", placeholder: "X-API-Key" },
                    { key: "api_key", label: "API Key", placeholder: "your-api-key", isSecret: true },
                ];
            case "bearer_token":
                return [
                    { key: "token", label: "Token", placeholder: "your-bearer-token", isSecret: true },
                ];
            case "basic_auth":
                return [
                    { key: "username", label: "Username", placeholder: "username" },
                    { key: "password", label: "Password", placeholder: "password", isSecret: true },
                ];
            case "custom_header":
                return [
                    { key: "header_name", label: "Header Name", placeholder: "X-Custom-Header" },
                    { key: "header_value", label: "Header Value", placeholder: "header-value", isSecret: true },
                ];
            default:
                return [];
        }
    };

    const addHeader = () => {
        setCustomHeaders([...customHeaders, { key: "", value: "" }]);
    };

    const updateHeader = (index: number, field: "key" | "value", value: string) => {
        const newHeaders = [...customHeaders];
        newHeaders[index] = { ...newHeaders[index], [field]: value };
        setCustomHeaders(newHeaders);
    };

    const removeHeader = (index: number) => {
        setCustomHeaders(customHeaders.filter((_, i) => i !== index));
    };

    const availableVariables = [
        { name: "workflow_run_id", description: "Unique ID of the workflow run" },
        { name: "workflow_id", description: "ID of the workflow" },
        { name: "workflow_name", description: "Name of the workflow" },
        { name: "initial_context.*", description: "Initial context variables" },
        { name: "gathered_context.*", description: "Extracted variables" },
        { name: "cost_info.call_duration_seconds", description: "Call duration" },
        { name: "recording_url", description: "Call recording URL" },
        { name: "transcript_url", description: "Transcript URL" },
    ];

    return (
        <Tabs defaultValue="basic" className="w-full">
            <TabsList className="grid w-full grid-cols-4">
                <TabsTrigger value="basic">Basic</TabsTrigger>
                <TabsTrigger value="auth">Auth</TabsTrigger>
                <TabsTrigger value="headers">Headers</TabsTrigger>
                <TabsTrigger value="payload">Payload</TabsTrigger>
            </TabsList>

            <TabsContent value="basic" className="space-y-4 mt-4">
                <div className="grid gap-2">
                    <Label>Name</Label>
                    <Label className="text-xs text-muted-foreground">
                        A display name for this webhook.
                    </Label>
                    <Input value={name} onChange={(e) => setName(e.target.value)} />
                </div>

                <div className="flex items-center space-x-2 p-2 border rounded-md bg-muted/20">
                    <Switch id="enabled" checked={enabled} onCheckedChange={setEnabled} />
                    <Label htmlFor="enabled">Enabled</Label>
                    <Label className="text-xs text-muted-foreground ml-2">
                        Whether this webhook is active.
                    </Label>
                </div>

                <div className="grid gap-2">
                    <Label>HTTP Method</Label>
                    <Select value={httpMethod} onValueChange={(v) => setHttpMethod(v as HttpMethod)}>
                        <SelectTrigger>
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="GET">GET</SelectItem>
                            <SelectItem value="POST">POST</SelectItem>
                            <SelectItem value="PUT">PUT</SelectItem>
                            <SelectItem value="PATCH">PATCH</SelectItem>
                            <SelectItem value="DELETE">DELETE</SelectItem>
                        </SelectContent>
                    </Select>
                </div>

                <div className="grid gap-2">
                    <Label>Endpoint URL</Label>
                    <Label className="text-xs text-muted-foreground">
                        The URL to send the webhook request to.
                    </Label>
                    <Input
                        value={endpointUrl}
                        onChange={(e) => setEndpointUrl(e.target.value)}
                        placeholder="https://api.example.com/webhook"
                    />
                </div>
            </TabsContent>

            <TabsContent value="auth" className="space-y-4 mt-4">
                <div className="grid gap-2">
                    <Label>Credential</Label>
                    <Label className="text-xs text-muted-foreground">
                        Select a credential for authentication, or leave empty for no auth.
                    </Label>
                    <div className="flex gap-2">
                        <Select
                            value={credentialUuid || "none"}
                            onValueChange={(v) => setCredentialUuid(v === "none" ? "" : v)}
                            disabled={credentialsLoading}
                        >
                            <SelectTrigger className="flex-1">
                                {credentialsLoading ? (
                                    <div className="flex items-center gap-2">
                                        <Loader2 className="h-4 w-4 animate-spin" />
                                        <span>Loading...</span>
                                    </div>
                                ) : (
                                    <SelectValue placeholder="No authentication" />
                                )}
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="none">No authentication</SelectItem>
                                {credentials.map((cred) => (
                                    <SelectItem key={cred.uuid} value={cred.uuid}>
                                        {cred.name} ({cred.credential_type})
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <Button
                            variant="outline"
                            size="icon"
                            onClick={() => setIsAddCredentialOpen(true)}
                            title="Add new credential"
                        >
                            <PlusIcon className="h-4 w-4" />
                        </Button>
                    </div>
                </div>

                {credentials.length === 0 && !credentialsLoading && (
                    <div className="p-3 border rounded-md bg-muted/20">
                        <p className="text-sm text-muted-foreground">
                            No credentials found. Click the + button to create one.
                        </p>
                    </div>
                )}

                {/* Add Credential Dialog */}
                <Dialog open={isAddCredentialOpen} onOpenChange={handleAddCredentialDialogChange}>
                    <DialogContent className="sm:max-w-md">
                        <DialogHeader>
                            <DialogTitle>Add Credential</DialogTitle>
                            <DialogDescription>
                                Create a new credential for webhook authentication.
                            </DialogDescription>
                        </DialogHeader>

                        {/* Error display */}
                        {credentialError && (
                            <div className="flex items-start gap-2 p-3 text-sm text-red-600 bg-red-50 border border-red-200 rounded-md">
                                <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                                <span>{credentialError}</span>
                            </div>
                        )}

                        <div className="space-y-4 py-4">
                            <div className="grid gap-2">
                                <Label htmlFor="cred-name">Name *</Label>
                                <Input
                                    id="cred-name"
                                    value={newCredName}
                                    onChange={(e) => setNewCredName(e.target.value)}
                                    placeholder="My API Key"
                                />
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor="cred-description">Description</Label>
                                <Input
                                    id="cred-description"
                                    value={newCredDescription}
                                    onChange={(e) => setNewCredDescription(e.target.value)}
                                    placeholder="Optional description"
                                />
                            </div>
                            <div className="grid gap-2">
                                <Label>Credential Type</Label>
                                <Select
                                    value={newCredType}
                                    onValueChange={(v) => {
                                        setNewCredType(v as WebhookCredentialType);
                                        setNewCredData({});
                                    }}
                                >
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="bearer_token">Bearer Token</SelectItem>
                                        <SelectItem value="api_key">API Key</SelectItem>
                                        <SelectItem value="basic_auth">Basic Auth</SelectItem>
                                        <SelectItem value="custom_header">Custom Header</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            {getCredentialDataFields(newCredType).map((field) => (
                                <div key={field.key} className="grid gap-2">
                                    <Label htmlFor={`cred-${field.key}`}>{field.label}</Label>
                                    <Input
                                        id={`cred-${field.key}`}
                                        type={field.isSecret ? "password" : "text"}
                                        value={newCredData[field.key] || ""}
                                        onChange={(e) =>
                                            setNewCredData((prev) => ({
                                                ...prev,
                                                [field.key]: e.target.value,
                                            }))
                                        }
                                        placeholder={field.placeholder}
                                    />
                                </div>
                            ))}
                        </div>
                        <DialogFooter>
                            <Button
                                variant="outline"
                                onClick={() => setIsAddCredentialOpen(false)}
                                disabled={isCreatingCredential}
                            >
                                Cancel
                            </Button>
                            <Button
                                onClick={handleCreateCredential}
                                disabled={!newCredName.trim() || isCreatingCredential}
                            >
                                {isCreatingCredential ? (
                                    <>
                                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                        Creating...
                                    </>
                                ) : (
                                    "Create"
                                )}
                            </Button>
                        </DialogFooter>
                    </DialogContent>
                </Dialog>
            </TabsContent>

            <TabsContent value="headers" className="space-y-4 mt-4">
                <div className="grid gap-2">
                    <Label>Custom Headers</Label>
                    <Label className="text-xs text-muted-foreground">
                        Add custom headers to include in the webhook request.
                    </Label>

                    {customHeaders.map((header, index) => (
                        <div key={index} className="flex items-center gap-2">
                            <Input
                                placeholder="Header name"
                                value={header.key}
                                onChange={(e) => updateHeader(index, "key", e.target.value)}
                                className="flex-1"
                            />
                            <Input
                                placeholder="Header value"
                                value={header.value}
                                onChange={(e) => updateHeader(index, "value", e.target.value)}
                                className="flex-1"
                            />
                            <Button
                                variant="outline"
                                size="icon"
                                onClick={() => removeHeader(index)}
                            >
                                <Trash2Icon className="h-4 w-4" />
                            </Button>
                        </div>
                    ))}

                    <Button variant="outline" size="sm" onClick={addHeader} className="w-fit">
                        <PlusIcon className="h-4 w-4 mr-1" /> Add Header
                    </Button>
                </div>
            </TabsContent>

            <TabsContent value="payload" className="space-y-4 mt-4">
                <JsonEditor
                    value={payloadTemplate}
                    onChange={setPayloadTemplate}
                    label="Payload Template (JSON)"
                    description='Define the JSON payload. Use "{{variable}}" syntax for dynamic values (must be quoted strings).'
                    placeholder='{"call_id": "{{workflow_run_id}}", "name": "{{initial_context.name}}"}'
                    minHeight="200px"
                />

                <div className="border rounded-md p-3 bg-muted/20">
                    <Label className="text-sm font-medium">Available Variables</Label>
                    <div className="mt-2 space-y-1">
                        {availableVariables.map((v) => (
                            <div key={v.name} className="text-xs">
                                <code className="bg-muted px-1 py-0.5 rounded">
                                    {`{{${v.name}}}`}
                                </code>
                                <span className="text-muted-foreground ml-2">{v.description}</span>
                            </div>
                        ))}
                    </div>
                </div>
            </TabsContent>
        </Tabs>
    );
};

WebhookNode.displayName = "WebhookNode";
