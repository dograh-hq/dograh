"use client";

import { ArrowDown, ArrowUp, ExternalLink, Plus, Trash2 } from "lucide-react";

import type { RecordingResponseSchema } from "@/client/types.gen";
import { RecordingSelect, StaticTextWarning } from "@/components/flow/TextOrAudioInput";
import {
    CredentialSelector,
    KeyValueEditor,
    type KeyValueItem,
    ParameterEditor,
    PresetParameterEditor,
    type PresetToolParameter,
    type ToolParameter,
    UrlInput,
} from "@/components/http";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { DOCS_BASE } from "@/constants/documentation";

import {
    type ContextDestinationRuleRow,
    createContextDestinationRouteRow,
    createContextDestinationRuleRow,
    type EndCallMessageType,
    type TransferDestinationSource,
} from "../../config";

export interface TransferCallToolConfigProps {
    name: string;
    onNameChange: (name: string) => void;
    description: string;
    onDescriptionChange: (description: string) => void;
    destinationSource: TransferDestinationSource;
    onDestinationSourceChange: (source: TransferDestinationSource) => void;
    destination: string;
    onDestinationChange: (destination: string) => void;
    messageType: EndCallMessageType;
    onMessageTypeChange: (messageType: EndCallMessageType) => void;
    customMessage: string;
    onCustomMessageChange: (message: string) => void;
    audioRecordingId: string;
    onAudioRecordingIdChange: (id: string) => void;
    recordings?: RecordingResponseSchema[];
    timeout?: number;
    onTimeoutChange: (timeout: number) => void;
    resolverUrl: string;
    onResolverUrlChange: (url: string) => void;
    resolverCredentialUuid: string;
    onResolverCredentialUuidChange: (uuid: string) => void;
    resolverHeaders: KeyValueItem[];
    onResolverHeadersChange: (headers: KeyValueItem[]) => void;
    resolverTimeoutMs: number;
    onResolverTimeoutMsChange: (timeoutMs: number) => void;
    resolverWaitMessage: string;
    onResolverWaitMessageChange: (message: string) => void;
    parameters: ToolParameter[];
    onParametersChange: (parameters: ToolParameter[]) => void;
    presetParameters: PresetToolParameter[];
    onPresetParametersChange: (parameters: PresetToolParameter[]) => void;
    contextDestinationRules: ContextDestinationRuleRow[];
    onContextDestinationRulesChange: (rules: ContextDestinationRuleRow[]) => void;
    fallbackDestination: string;
    onFallbackDestinationChange: (destination: string) => void;
}

export function TransferCallToolConfig({
    name,
    onNameChange,
    description,
    onDescriptionChange,
    destinationSource,
    onDestinationSourceChange,
    destination,
    onDestinationChange,
    messageType,
    onMessageTypeChange,
    customMessage,
    onCustomMessageChange,
    audioRecordingId,
    onAudioRecordingIdChange,
    recordings = [],
    timeout,
    onTimeoutChange,
    resolverUrl,
    onResolverUrlChange,
    resolverCredentialUuid,
    onResolverCredentialUuidChange,
    resolverHeaders,
    onResolverHeadersChange,
    resolverTimeoutMs,
    onResolverTimeoutMsChange,
    resolverWaitMessage,
    onResolverWaitMessageChange,
    parameters,
    onParametersChange,
    presetParameters,
    onPresetParametersChange,
    contextDestinationRules,
    onContextDestinationRulesChange,
    fallbackDestination,
    onFallbackDestinationChange,
}: TransferCallToolConfigProps) {
    const updateRule = (
        ruleId: string,
        update: (rule: ContextDestinationRuleRow) => ContextDestinationRuleRow,
    ) => {
        onContextDestinationRulesChange(
            contextDestinationRules.map((rule) => (rule.id === ruleId ? update(rule) : rule))
        );
    };

    const moveRule = (index: number, offset: number) => {
        const target = index + offset;
        if (target < 0 || target >= contextDestinationRules.length) return;
        const reordered = [...contextDestinationRules];
        [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
        onContextDestinationRulesChange(reordered);
    };

    return (
        <Card>
            <CardHeader>
                <CardTitle>Transfer Call Configuration</CardTitle>
                <CardDescription>
                    Configure call transfer settings. Supports phone numbers (Twilio, Plivo) and SIP endpoints (Asterisk ARI).
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
                <div className="grid gap-2">
                    <Label>Tool Name</Label>
                    <Label className="text-xs text-muted-foreground">
                        A descriptive name for this tool
                    </Label>
                    <Input
                        value={name}
                        onChange={(e) => onNameChange(e.target.value)}
                        placeholder="e.g., Transfer Call"
                    />
                </div>

                <div className="grid gap-2">
                    <Label>Description</Label>
                    <Label className="text-xs text-muted-foreground">
                        Helps the LLM understand when to use this tool
                    </Label>
                    <Textarea
                        value={description}
                        onChange={(e) => onDescriptionChange(e.target.value)}
                        placeholder="When should the AI transfer the call?"
                        rows={3}
                    />
                </div>

                <div className="grid gap-4 pt-4 border-t">
                    <Label>Pre-Transfer Message</Label>
                    <Label className="text-xs text-muted-foreground">
                        Choose whether to play a configured message before transferring. In dynamic mode, resolver custom_message overrides this when returned.
                    </Label>
                    <RadioGroup
                        value={messageType}
                        onValueChange={(v) => onMessageTypeChange(v as EndCallMessageType)}
                        className="space-y-3"
                    >
                        <label
                            htmlFor="none"
                            className="flex items-center space-x-3 p-3 border rounded-lg hover:bg-muted/50 cursor-pointer"
                        >
                            <RadioGroupItem value="none" id="none" />
                            <div className="flex-1">
                                <span className="font-medium">No Message</span>
                                <p className="text-xs text-muted-foreground">
                                    Transfer the call immediately without any message
                                </p>
                            </div>
                        </label>
                        <div className="flex items-start space-x-3 p-3 border rounded-lg hover:bg-muted/50">
                            <RadioGroupItem value="custom" id="custom" className="mt-1" />
                            <label htmlFor="custom" className="flex-1 space-y-2 cursor-pointer">
                                <span className="font-medium">Custom Message</span>
                                <p className="text-xs text-muted-foreground">
                                    Play a custom message before transferring
                                </p>
                            </label>
                        </div>
                        {messageType === "custom" && (
                            <div className="pl-8 space-y-2">
                                <StaticTextWarning />
                                <Textarea
                                    value={customMessage}
                                    onChange={(e) => onCustomMessageChange(e.target.value)}
                                    placeholder="e.g., Please hold while I transfer your call."
                                    rows={2}
                                />
                            </div>
                        )}
                        <div className="flex items-start space-x-3 p-3 border rounded-lg hover:bg-muted/50">
                            <RadioGroupItem value="audio" id="audio" className="mt-1" />
                            <label htmlFor="audio" className="flex-1 space-y-2 cursor-pointer">
                                <span className="font-medium">Pre-recorded Audio</span>
                                <p className="text-xs text-muted-foreground">
                                    Play a pre-recorded audio file before transferring
                                </p>
                            </label>
                        </div>
                        {messageType === "audio" && (
                            <div className="pl-8">
                                <RecordingSelect
                                    value={audioRecordingId}
                                    onChange={onAudioRecordingIdChange}
                                    recordings={recordings}
                                />
                            </div>
                        )}
                    </RadioGroup>
                </div>

                <div className="grid gap-2 pt-4 border-t">
                    <Label>Transfer Timeout</Label>
                    <Label className="text-xs text-muted-foreground">
                        Maximum time to wait for destination to answer after the transfer starts (5-120 seconds)
                    </Label>
                    <Input
                        type="number"
                        value={timeout ?? 30}
                        onChange={(e) => {
                            const value = parseInt(e.target.value) || 30;
                            onTimeoutChange(Math.min(Math.max(value, 5), 120));
                        }}
                        placeholder="30"
                        min="5"
                        max="120"
                        className="w-32"
                    />
                    <Label className="text-xs text-muted-foreground">
                        Default: 30 seconds
                    </Label>
                </div>

                <div className="grid gap-4 pt-4 border-t">
                    <div>
                        <Label>Destination Source</Label>
                        <p className="text-xs text-muted-foreground">
                            Choose a configured destination, ordered context rules, or an HTTP resolver.
                        </p>
                    </div>
                    <Tabs
                        value={destinationSource}
                        onValueChange={(v) => onDestinationSourceChange(v as TransferDestinationSource)}
                        className="w-full"
                    >
                        <TabsList className="grid w-full grid-cols-3">
                            <TabsTrigger value="static">Static / Template</TabsTrigger>
                            <TabsTrigger value="dynamic">Dynamic HTTP Resolver</TabsTrigger>
                            <TabsTrigger value="context_mapping">Context Mapping</TabsTrigger>
                        </TabsList>

                        <TabsContent value="static" className="space-y-4 mt-4">
                            <div className="grid gap-2">
                                <Label>Transfer Destination</Label>
                                <div className="text-xs text-muted-foreground space-y-1">
                                    <p>Use a fixed number, SIP endpoint, or context template.</p>
                                    <ul className="list-disc pl-4 space-y-1">
                                        <li>SIP endpoint, e.g. PJSIP/1234</li>
                                        <li>E.164 phone number, e.g. +1234567890</li>
                                        <li>
                                            Template variable, e.g. {"{{initial_context.transfer_destination}}"}
                                        </li>
                                    </ul>
                                </div>
                                <Input
                                    value={destination}
                                    onChange={(e) => onDestinationChange(e.target.value)}
                                    placeholder="+1234567890, PJSIP/1234, or {{initial_context.transfer_destination}}"
                                />
                            </div>
                        </TabsContent>

                        <TabsContent value="dynamic" className="space-y-5 mt-4">
                            <div>
                                <div className="flex items-center gap-2">
                                    <Label>Dynamic Transfer Resolver</Label>
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    Dograh sends the resolved argument dictionary to this endpoint. The endpoint must return transfer_context.destination and may return transfer_context.custom_message.{" "}
                                    <a
                                        href={`${DOCS_BASE}/voice-agent/tools/call-transfer#dynamic-resolver-response`}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="inline-flex items-center gap-1 transition-colors hover:text-foreground"
                                    >
                                        Docs <ExternalLink className="h-3 w-3" />
                                    </a>
                                </p>
                            </div>

                            <div className="grid gap-2">
                                <Label>Resolver URL</Label>
                                <UrlInput
                                    value={resolverUrl}
                                    onChange={onResolverUrlChange}
                                    placeholder="https://crm.example.com/resolve-transfer"
                                    showValidation
                                />
                                <Label className="text-xs text-muted-foreground">
                                    Dograh sends a POST request with the resolved argument dictionary.
                                </Label>
                            </div>

                            <div className="grid gap-2">
                                <Label>Resolver Timeout</Label>
                                <Input
                                    type="number"
                                    value={resolverTimeoutMs}
                                    onChange={(e) => {
                                        const value = parseInt(e.target.value) || 3000;
                                        onResolverTimeoutMsChange(Math.min(Math.max(value, 500), 5000));
                                    }}
                                    min="500"
                                    max="5000"
                                    className="w-36"
                                />
                                <Label className="text-xs text-muted-foreground">
                                    Default: 3000 ms. Maximum: 5000 ms.
                                </Label>
                            </div>

                            <CredentialSelector
                                value={resolverCredentialUuid}
                                onChange={onResolverCredentialUuidChange}
                                label="Resolver Credential (Optional)"
                                description="Select a credential for the resolver endpoint, or leave empty for no auth."
                            />

                            <div className="grid gap-2">
                                <Label>Resolver Wait Message</Label>
                                <Textarea
                                    value={resolverWaitMessage}
                                    onChange={(e) => onResolverWaitMessageChange(e.target.value)}
                                    placeholder="One moment while I find the right team."
                                    rows={2}
                                />
                                <Label className="text-xs text-muted-foreground">
                                    Spoken while Dograh waits for the resolver response.
                                </Label>
                            </div>

                            <div className="grid gap-2 pt-4 border-t">
                                <Label>LLM Parameters</Label>
                                <p className="text-xs text-muted-foreground">
                                    Define values the agent should provide when calling this transfer tool, such as state, department, or reason.{" "}
                                    <a
                                        href={`${DOCS_BASE}/voice-agent/tools/call-transfer#dynamic-resolver-request`}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="inline-flex items-center gap-1 transition-colors hover:text-foreground"
                                    >
                                        Docs <ExternalLink className="h-3 w-3" />
                                    </a>
                                </p>
                                <ParameterEditor
                                    parameters={parameters}
                                    onChange={onParametersChange}
                                />
                            </div>

                            <div className="grid gap-2 pt-4 border-t">
                                <Label>Preset Parameters</Label>
                                <p className="text-xs text-muted-foreground">
                                    Add values Dograh injects at runtime. These are not exposed to the LLM and can use templates like {`{{initial_context.state}}`} or {`{{gathered_context.state}}`}.{" "}
                                    <a
                                        href={`${DOCS_BASE}/voice-agent/tools/call-transfer#dynamic-resolver-request`}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="inline-flex items-center gap-1 transition-colors hover:text-foreground"
                                    >
                                        Docs <ExternalLink className="h-3 w-3" />
                                    </a>
                                </p>
                                <PresetParameterEditor
                                    parameters={presetParameters}
                                    onChange={onPresetParametersChange}
                                />
                            </div>

                            <div className="grid gap-2 pt-4 border-t">
                                <Label>Custom Headers</Label>
                                <Label className="text-xs text-muted-foreground">
                                    Add custom headers for authentication or routing metadata.
                                </Label>
                                <KeyValueEditor
                                    items={resolverHeaders}
                                    onChange={onResolverHeadersChange}
                                    keyPlaceholder="Header name"
                                    valuePlaceholder="Header value"
                                    addButtonText="Add Header"
                                />
                            </div>
                        </TabsContent>
                        <TabsContent value="context_mapping" className="space-y-5 mt-4">
                            <div className="space-y-2">
                                <Label>Ordered Context Routing</Label>
                                <p className="text-xs text-muted-foreground">
                                    Rules are evaluated top to bottom. The first matching value selects its
                                    destination; matching ignores case and surrounding whitespace.
                                </p>
                                <p className="text-xs text-muted-foreground">
                                    An unprefixed field such as <code>department</code> checks{" "}
                                    <code>gathered_context.department</code> first, then{" "}
                                    <code>initial_context.department</code>. Use an explicit prefix to read only
                                    one context.
                                </p>
                                <p className="text-xs text-muted-foreground">
                                    Destinations can be a SIP endpoint, E.164 PSTN number, another
                                    provider-supported destination, or a template such as{" "}
                                    <code>{"{{initial_context.transfer_destination}}"}</code>.{" "}
                                    <a
                                        href={`${DOCS_BASE}/voice-agent/tools/call-transfer#context-mapping`}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="inline-flex items-center gap-1 transition-colors hover:text-foreground"
                                    >
                                        Docs <ExternalLink className="h-3 w-3" />
                                    </a>
                                </p>
                            </div>
                                {contextDestinationRules.map((rule, ruleIndex) => (
                                    <div key={rule.id} className="space-y-4 rounded-lg border p-4">
                                        <div className="flex items-center justify-between">
                                            <Label>Rule {ruleIndex + 1}</Label>
                                            <div className="flex items-center gap-1">
                                                <Button
                                                    type="button"
                                                    variant="ghost"
                                                    size="icon"
                                                    aria-label={`Move rule ${ruleIndex + 1} up`}
                                                    disabled={ruleIndex === 0}
                                                    onClick={() => moveRule(ruleIndex, -1)}
                                                >
                                                    <ArrowUp className="h-4 w-4" />
                                                </Button>
                                                <Button
                                                    type="button"
                                                    variant="ghost"
                                                    size="icon"
                                                    aria-label={`Move rule ${ruleIndex + 1} down`}
                                                    disabled={ruleIndex === contextDestinationRules.length - 1}
                                                    onClick={() => moveRule(ruleIndex, 1)}
                                                >
                                                    <ArrowDown className="h-4 w-4" />
                                                </Button>
                                                <Button
                                                    type="button"
                                                    variant="ghost"
                                                    size="icon"
                                                    aria-label={`Remove rule ${ruleIndex + 1}`}
                                                    onClick={() => onContextDestinationRulesChange(
                                                        contextDestinationRules.filter((item) => item.id !== rule.id)
                                                    )}
                                                >
                                                    <Trash2 className="h-4 w-4" />
                                                </Button>
                                            </div>
                                        </div>
                                        <div className="grid gap-2">
                                            <Label htmlFor={`context-path-${rule.id}`}>
                                                Context Field
                                            </Label>
                                            <Input
                                                id={`context-path-${rule.id}`}
                                                aria-label={`Context field ${ruleIndex + 1}`}
                                                value={rule.context_path}
                                                onChange={(event) => updateRule(rule.id, (item) => ({
                                                    ...item,
                                                    context_path: event.target.value,
                                                }))}
                                                placeholder="department or initial_context.department"
                                            />
                                        </div>
                                        <div className="space-y-3">
                                            <div className="flex items-center justify-between">
                                                <Label>Value to Destination Mappings</Label>
                                                <Button
                                                    type="button"
                                                    variant="outline"
                                                    size="sm"
                                                    aria-label={`Add mapping to rule ${ruleIndex + 1}`}
                                                    onClick={() => updateRule(rule.id, (item) => ({
                                                        ...item,
                                                        routes: [
                                                            ...item.routes,
                                                            createContextDestinationRouteRow(),
                                                        ],
                                                    }))}
                                                >
                                                    <Plus className="mr-1 h-4 w-4" /> Add mapping
                                                </Button>
                                            </div>
                                            {rule.routes.map((route, index) => (
                                                <div key={route.id} className="grid grid-cols-[1fr_1fr_auto] gap-2">
                                                    <Input
                                                        aria-label={`Rule ${ruleIndex + 1} context value ${index + 1}`}
                                                        value={route.context_value}
                                                        onChange={(event) => updateRule(rule.id, (item) => ({
                                                            ...item,
                                                            routes: item.routes.map((existing) =>
                                                                existing.id === route.id
                                                                    ? { ...existing, context_value: event.target.value }
                                                                    : existing
                                                            ),
                                                        }))}
                                                        placeholder="Context value"
                                                    />
                                                    <Input
                                                        aria-label={`Rule ${ruleIndex + 1} transfer destination ${index + 1}`}
                                                        value={route.destination}
                                                        onChange={(event) => updateRule(rule.id, (item) => ({
                                                            ...item,
                                                            routes: item.routes.map((existing) =>
                                                                existing.id === route.id
                                                                    ? { ...existing, destination: event.target.value }
                                                                    : existing
                                                            ),
                                                        }))}
                                                        placeholder="PJSIP/1001, +1234567890, or {{initial_context.destination}}"
                                                    />
                                                    <Button
                                                        type="button"
                                                        variant="ghost"
                                                        size="icon"
                                                        aria-label={`Remove rule ${ruleIndex + 1} mapping ${index + 1}`}
                                                        onClick={() => updateRule(rule.id, (item) => ({
                                                            ...item,
                                                            routes: item.routes.filter(
                                                                (existing) => existing.id !== route.id
                                                            ),
                                                        }))}
                                                    >
                                                        <Trash2 className="h-4 w-4" />
                                                    </Button>
                                                </div>
                                            ))}
                                            {rule.routes.length === 0 && (
                                                <p className="text-xs text-muted-foreground">
                                                    Add at least one mapping.
                                                </p>
                                            )}
                                        </div>
                                    </div>
                                ))}
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    className="w-fit"
                                    onClick={() => onContextDestinationRulesChange([
                                        ...contextDestinationRules,
                                        createContextDestinationRuleRow(),
                                    ])}
                                >
                                    <Plus className="mr-1 h-4 w-4" /> Add routing rule
                                </Button>
                                {contextDestinationRules.length === 0 && (
                                    <p className="text-xs text-muted-foreground">
                                        Add at least one routing rule.
                                    </p>
                                )}
                                <div className="grid gap-2">
                                    <Label htmlFor="context-fallback-destination">Fallback Destination (Optional)</Label>
                                    <Input
                                        id="context-fallback-destination"
                                        value={fallbackDestination}
                                        onChange={(event) => onFallbackDestinationChange(event.target.value)}
                                        placeholder="Provider destination or {{initial_context.destination}}"
                                    />
                                    <Label className="text-xs text-muted-foreground">
                                        Used only when no rule above matched.
                                    </Label>
                                </div>
                        </TabsContent>
                    </Tabs>
                </div>
            </CardContent>
        </Card>
    );
}
