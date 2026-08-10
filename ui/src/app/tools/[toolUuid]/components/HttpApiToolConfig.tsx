"use client";

import { Info } from "lucide-react";

import type { RecordingResponseSchema } from "@/client/types.gen";
import { StaticTextWarning, TextOrAudioInput } from "@/components/flow/TextOrAudioInput";
import {
    CredentialSelector,
    extractUrlHostnameParameters,
    extractUrlPathParameters,
    type HttpMethod,
    HttpMethodSelector,
    KeyValueEditor,
    type KeyValueItem,
    ParameterEditor,
    PresetParameterEditor,
    type PresetToolParameter,
    type ToolParameter,
    UrlInput,
} from "@/components/http";
import { BodyTemplateEditor } from "@/components/http/body-template-editor";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
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
    presetParameters: PresetToolParameter[];
    onPresetParametersChange: (parameters: PresetToolParameter[]) => void;
    bodyTemplateEnabled: boolean;
    onBodyTemplateEnabledChange: (enabled: boolean) => void;
    bodyTemplate: Record<string, unknown> | null;
    onBodyTemplateChange: (template: Record<string, unknown> | null) => void;
    onBodyTemplateValidityChange: (valid: boolean) => void;
    timeoutMs: number;
    onTimeoutMsChange: (timeout: number) => void;
    customMessage: string;
    onCustomMessageChange: (message: string) => void;
    customMessageType: 'text' | 'audio';
    onCustomMessageTypeChange: (type: 'text' | 'audio') => void;
    customMessageRecordingId: string;
    onCustomMessageRecordingIdChange: (id: string) => void;
    recordings?: RecordingResponseSchema[];
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
    presetParameters,
    onPresetParametersChange,
    bodyTemplateEnabled,
    onBodyTemplateEnabledChange,
    bodyTemplate,
    onBodyTemplateChange,
    onBodyTemplateValidityChange,
    timeoutMs,
    onTimeoutMsChange,
    customMessage,
    onCustomMessageChange,
    customMessageType,
    onCustomMessageTypeChange,
    customMessageRecordingId,
    onCustomMessageRecordingIdChange,
    recordings = [],
}: HttpApiToolConfigProps) {
    const urlHostnameParameters = extractUrlHostnameParameters(url);
    const urlPathParameters = extractUrlPathParameters(url);

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
                            {urlHostnameParameters.length > 0 && (
                                <div className="rounded-lg border border-blue-500/20 bg-blue-500/10 p-3 text-sm text-blue-600 flex gap-2 items-start mt-2">
                                    <Info className="h-4 w-4 mt-0.5 shrink-0" />
                                    <span>
                                        Hostname parameters detected: {urlHostnameParameters.join(", ")}. Values resolve from tool call arguments or workflow context at runtime.
                                    </span>
                                </div>
                            )}
                            {urlPathParameters.length > 0 && (
                                <div className="rounded-lg border border-blue-500/20 bg-blue-500/10 p-3 text-sm text-blue-600 flex gap-2 items-start mt-2">
                                    <Info className="h-4 w-4 mt-0.5 shrink-0" />
                                    <span>
                                        Path parameters detected: {urlPathParameters.join(", ")}. Values resolve from tool call arguments or workflow context at runtime.
                                    </span>
                                </div>
                            )}
                        </div>

                        <div className="grid gap-2 pt-4 border-t">
                            <Label>Custom Message</Label>
                            <Label className="text-xs text-muted-foreground">
                                Optional message the AI will speak or play before executing this tool.
                            </Label>
                            <TextOrAudioInput
                                type={customMessageType}
                                onTypeChange={onCustomMessageTypeChange}
                                recordingId={customMessageRecordingId}
                                onRecordingIdChange={onCustomMessageRecordingIdChange}
                                recordings={recordings}
                            >
                                <>
                                    <StaticTextWarning />
                                    <Textarea
                                        value={customMessage}
                                        onChange={(e) => onCustomMessageChange(e.target.value)}
                                        placeholder="e.g., Let me check that for you, one moment please."
                                        rows={2}
                                    />
                                </>
                            </TextOrAudioInput>
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
                            <Label>LLM Parameters</Label>
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
                            <Label>Preset Parameters</Label>
                            <Label className="text-xs text-muted-foreground">
                                Add values that Dograh should inject at runtime. These are not exposed to the LLM and can use
                                workflow templates like {`{{initial_context.phone_number}}`} or fixed literals.
                            </Label>
                            <PresetParameterEditor
                                parameters={presetParameters}
                                onChange={onPresetParametersChange}
                            />
                        </div>

                        {["POST", "PUT", "PATCH"].includes(httpMethod) && (
                            <div className="grid gap-4 pt-4 border-t">
                                <div className="flex items-center justify-between gap-4">
                                    <div className="grid gap-1">
                                        <Label htmlFor="body-template-enabled">
                                            Tool Body Template
                                        </Label>
                                        <p className="text-xs text-muted-foreground">
                                            Enable a custom JSON request body.
                                        </p>
                                    </div>
                                    <Switch
                                        id="body-template-enabled"
                                        checked={bodyTemplateEnabled}
                                        onCheckedChange={onBodyTemplateEnabledChange}
                                    />
                                </div>
                                {bodyTemplateEnabled && (
                                    <BodyTemplateEditor
                                        value={bodyTemplate}
                                        onChange={onBodyTemplateChange}
                                        onValidityChange={onBodyTemplateValidityChange}
                                    />
                                )}
                            </div>
                        )}

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
