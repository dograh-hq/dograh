import { NodeProps, NodeToolbar, Position } from "@xyflow/react";
import { Circle, Edit, Link2, Trash2Icon } from "lucide-react";
import { memo, useEffect, useState } from "react";

import { useWorkflow } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import { FlowNodeData } from "@/components/flow/types";
import {
    CredentialSelector,
    type HttpMethod,
    HttpMethodSelector,
    KeyValueEditor,
    type KeyValueItem,
} from "@/components/http";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { JsonEditor, validateJson } from "@/components/ui/json-editor";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { NodeContent } from "./common/NodeContent";
import { NodeEditDialog } from "./common/NodeEditDialog";
import { useNodeHandlers } from "./common/useNodeHandlers";

interface WebhookNodeProps extends NodeProps {
    data: FlowNodeData;
}

export const WebhookNode = memo(({ data, selected, id }: WebhookNodeProps) => {
    const { open, setOpen, handleSaveNodeData, handleDeleteNode } = useNodeHandlers({ id });
    const { saveWorkflow } = useWorkflow();

    // Form state
    const [name, setName] = useState(data.name || "Webhook");
    const [enabled, setEnabled] = useState(data.enabled ?? true);
    const [httpMethod, setHttpMethod] = useState<HttpMethod>(data.http_method || "POST");
    const [endpointUrl, setEndpointUrl] = useState(data.endpoint_url || "");
    const [credentialUuid, setCredentialUuid] = useState(data.credential_uuid || "");
    const [customHeaders, setCustomHeaders] = useState<KeyValueItem[]>(
        data.custom_headers || []
    );
    const [payloadTemplate, setPayloadTemplate] = useState(
        data.payload_template ? JSON.stringify(data.payload_template, null, 2) : "{}"
    );

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
    customHeaders: KeyValueItem[];
    setCustomHeaders: (value: KeyValueItem[]) => void;
    payloadTemplate: string;
    setPayloadTemplate: (value: string) => void;
}

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
    customHeaders,
    setCustomHeaders,
    payloadTemplate,
    setPayloadTemplate,
}: WebhookNodeEditFormProps) => {
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
                    <HttpMethodSelector
                        value={httpMethod}
                        onChange={setHttpMethod}
                    />
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
                <CredentialSelector
                    value={credentialUuid}
                    onChange={setCredentialUuid}
                />
            </TabsContent>

            <TabsContent value="headers" className="space-y-4 mt-4">
                <div className="grid gap-2">
                    <Label>Custom Headers</Label>
                    <Label className="text-xs text-muted-foreground">
                        Add custom headers to include in the webhook request.
                    </Label>
                    <KeyValueEditor
                        items={customHeaders}
                        onChange={setCustomHeaders}
                        keyPlaceholder="Header name"
                        valuePlaceholder="Header value"
                        addButtonText="Add Header"
                    />
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
