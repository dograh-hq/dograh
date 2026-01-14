"use client";

import {
    CredentialSelector,
    type HttpMethod,
    HttpMethodSelector,
    KeyValueEditor,
    type KeyValueItem,
    ParameterEditor,
    type ToolParameter,
    UrlInput,
} from "@/components/http";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

export interface HttpApiToolConfigProps {
    name: string;
    onNameChange: (name: string) => void;
    description: string;
    onDescriptionChange: (description: string) => void;
    httpMethod: HttpMethod;
    onHttpMethodChange: (method: HttpMethod) => void;
    url: string;
    onUrlChange: (url: string) => void;
    credentialUuid: string;
    onCredentialUuidChange: (uuid: string) => void;
    headers: KeyValueItem[];
    onHeadersChange: (headers: KeyValueItem[]) => void;
    parameters: ToolParameter[];
    onParametersChange: (parameters: ToolParameter[]) => void;
    timeoutMs: number;
    onTimeoutMsChange: (timeout: number) => void;
}

export function HttpApiToolConfig({
    name,
    onNameChange,
    description,
    onDescriptionChange,
    httpMethod,
    onHttpMethodChange,
    url,
    onUrlChange,
    credentialUuid,
    onCredentialUuidChange,
    headers,
    onHeadersChange,
    parameters,
    onParametersChange,
    timeoutMs,
    onTimeoutMsChange,
}: HttpApiToolConfigProps) {
    return (
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
                                onChange={(e) => onNameChange(e.target.value)}
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
                                onChange={(e) => onDescriptionChange(e.target.value)}
                                placeholder="What does this tool do?"
                                rows={3}
                            />
                        </div>

                        <div className="grid grid-cols-2 gap-4">
                            <div className="grid gap-2">
                                <Label>HTTP Method</Label>
                                <HttpMethodSelector
                                    value={httpMethod}
                                    onChange={onHttpMethodChange}
                                />
                            </div>
                            <div className="grid gap-2">
                                <Label>Timeout (ms)</Label>
                                <Input
                                    type="number"
                                    value={timeoutMs}
                                    onChange={(e) =>
                                        onTimeoutMsChange(parseInt(e.target.value) || 5000)
                                    }
                                    min={1000}
                                    max={30000}
                                />
                            </div>
                        </div>

                        <div className="grid gap-2">
                            <Label>Endpoint URL</Label>
                            <UrlInput
                                value={url}
                                onChange={onUrlChange}
                                placeholder="https://api.example.com/appointments"
                                showValidation
                            />
                        </div>
                    </TabsContent>

                    <TabsContent value="auth" className="space-y-4 mt-4">
                        <CredentialSelector
                            value={credentialUuid}
                            onChange={onCredentialUuidChange}
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
                                onChange={onParametersChange}
                            />
                        </div>

                        <div className="grid gap-2 pt-4 border-t">
                            <Label>Custom Headers</Label>
                            <Label className="text-xs text-muted-foreground">
                                Add custom headers to include in the request (optional)
                            </Label>
                            <KeyValueEditor
                                items={headers}
                                onChange={onHeadersChange}
                                keyPlaceholder="Header name"
                                valuePlaceholder="Header value"
                                addButtonText="Add Header"
                            />
                        </div>
                    </TabsContent>
                </Tabs>
            </CardContent>
        </Card>
    );
}
