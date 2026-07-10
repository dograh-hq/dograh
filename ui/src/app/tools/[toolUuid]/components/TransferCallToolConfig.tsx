"use client";

import type { RecordingResponseSchema } from "@/client/types.gen";
import { CredentialSelector, ParameterEditor, type ToolParameter } from "@/components/http";
import { RecordingSelect, StaticTextWarning } from "@/components/flow/TextOrAudioInput";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Plus, Trash2 } from "lucide-react";

import {
    type EndCallMessageType,
    type TransferApprovedRouteRow,
    type TransferResolverPolicy,
} from "../../config";

export interface TransferCallToolConfigProps {
    name: string;
    onNameChange: (name: string) => void;
    description: string;
    onDescriptionChange: (description: string) => void;
    destination: string;
    onDestinationChange: (destination: string) => void;
    messageType: EndCallMessageType;
    onMessageTypeChange: (messageType: EndCallMessageType) => void;
    customMessage: string;
    onCustomMessageChange: (message: string) => void;
    audioRecordingId: string;
    onAudioRecordingIdChange: (id: string) => void;
    recordings?: RecordingResponseSchema[];
    timeout?: number;  // Make optional to match API type
    onTimeoutChange: (timeout: number) => void;
    resolverEnabled: boolean;
    onResolverEnabledChange: (enabled: boolean) => void;
    resolverUrl: string;
    onResolverUrlChange: (url: string) => void;
    resolverCredentialUuid: string;
    onResolverCredentialUuidChange: (uuid: string) => void;
    resolverTimeoutMs: number;
    onResolverTimeoutMsChange: (timeoutMs: number) => void;
    resolverWaitMessage: string;
    onResolverWaitMessageChange: (message: string) => void;
    resolverPolicy: TransferResolverPolicy;
    onResolverPolicyChange: (policy: TransferResolverPolicy) => void;
    approvedRoutes: TransferApprovedRouteRow[];
    onApprovedRoutesChange: (routes: TransferApprovedRouteRow[]) => void;
    fallbackRoute: string;
    onFallbackRouteChange: (route: string) => void;
    parameters: ToolParameter[];
    onParametersChange: (parameters: ToolParameter[]) => void;
}

export function TransferCallToolConfig({
    name,
    onNameChange,
    description,
    onDescriptionChange,
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
    resolverEnabled,
    onResolverEnabledChange,
    resolverUrl,
    onResolverUrlChange,
    resolverCredentialUuid,
    onResolverCredentialUuidChange,
    resolverTimeoutMs,
    onResolverTimeoutMsChange,
    resolverWaitMessage,
    onResolverWaitMessageChange,
    resolverPolicy,
    onResolverPolicyChange,
    approvedRoutes,
    onApprovedRoutesChange,
    fallbackRoute,
    onFallbackRouteChange,
    parameters,
    onParametersChange,
}: TransferCallToolConfigProps) {
    const updateRoute = (
        index: number,
        patch: Partial<TransferApprovedRouteRow>,
    ) => {
        onApprovedRoutesChange(
            approvedRoutes.map((route, i) =>
                i === index ? { ...route, ...patch } : route,
            ),
        );
    };

    const removeRoute = (index: number) => {
        onApprovedRoutesChange(approvedRoutes.filter((_, i) => i !== index));
    };

    const addRoute = () => {
        onApprovedRoutesChange([
            ...approvedRoutes,
            {
                key: "",
                destination: "",
                message: "",
                timeout_seconds: 30,
            },
        ]);
    };

    return (
        <Card>
            <CardHeader>
                <CardTitle>Transfer Call Configuration</CardTitle>
                <CardDescription>
                    Configure call transfer settings. Supports phone numbers (Twilio) and SIP endpoints (Asterisk ARI).
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

                <div className="grid gap-2 pt-4 border-t">
                    <Label>Transfer Destination</Label>
                    <div className="text-xs text-muted-foreground space-y-1">
                        <p>Enter one of these destination formats:</p>
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
                        placeholder={
                            "+1234567890, PJSIP/1234, or {{initial_context.transfer_destination}}"
                        }
                    />
                </div>

                <div className="grid gap-4 pt-4 border-t">
                    <Label>Pre-Transfer Message</Label>
                    <Label className="text-xs text-muted-foreground">
                        Choose whether to play a message before transferring
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
                        Maximum time to wait for destination to answer (5-120 seconds)
                    </Label>
                    <Input
                        type="number"
                        value={timeout ?? 30}
                        onChange={(e) => {
                            const value = parseInt(e.target.value) || 30;
                            // Clamp value between 5 and 120 seconds
                            const clampedValue = Math.min(Math.max(value, 5), 120);
                            onTimeoutChange(clampedValue);
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
                    <div className="flex items-center justify-between gap-4">
                        <div className="space-y-1">
                            <Label>Dynamic Transfer Resolver</Label>
                            <p className="text-xs text-muted-foreground">
                                Resolve the destination from an HTTP endpoint when the transfer tool is called.
                            </p>
                        </div>
                        <Switch
                            checked={resolverEnabled}
                            onCheckedChange={onResolverEnabledChange}
                        />
                    </div>

                    {resolverEnabled && (
                        <div className="space-y-5">
                            <div className="grid gap-2">
                                <Label>Resolver URL</Label>
                                <Input
                                    value={resolverUrl}
                                    onChange={(e) => onResolverUrlChange(e.target.value)}
                                    placeholder="https://crm.example.com/resolve-transfer"
                                />
                            </div>

                            <CredentialSelector
                                value={resolverCredentialUuid}
                                onChange={onResolverCredentialUuidChange}
                                label="Resolver Credential (Optional)"
                                description="Select a credential for the resolver endpoint, or leave empty for no auth."
                            />

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

                            <div className="grid gap-2">
                                <Label>Resolver Wait Message</Label>
                                <Textarea
                                    value={resolverWaitMessage}
                                    onChange={(e) => onResolverWaitMessageChange(e.target.value)}
                                    placeholder="One moment while I find the right team."
                                    rows={2}
                                />
                            </div>

                            <div className="grid gap-2">
                                <Label>Resolver Policy</Label>
                                <Select
                                    value={resolverPolicy}
                                    onValueChange={(value) =>
                                        onResolverPolicyChange(value as TransferResolverPolicy)
                                    }
                                >
                                    <SelectTrigger>
                                        <SelectValue placeholder="Select policy" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="approved_routes_only">
                                            Approved routes only
                                        </SelectItem>
                                        <SelectItem value="approved_routes_or_static_fallback">
                                            Approved routes or static fallback
                                        </SelectItem>
                                        <SelectItem value="allow_raw_destination">
                                            Allow raw destination
                                        </SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>

                            <div className="grid gap-3">
                                <div>
                                    <Label>Resolver Arguments</Label>
                                    <p className="text-xs text-muted-foreground">
                                        Values the agent should pass when calling this transfer tool, such as state, department, or reason.
                                    </p>
                                </div>
                                <ParameterEditor
                                    parameters={parameters}
                                    onChange={onParametersChange}
                                />
                            </div>

                            <div className="grid gap-2">
                                <Label>Fallback Route</Label>
                                <Input
                                    value={fallbackRoute}
                                    onChange={(e) => onFallbackRouteChange(e.target.value)}
                                    placeholder="referral_default"
                                />
                            </div>

                            <div className="space-y-3">
                                <div className="flex items-center justify-between gap-3">
                                    <div>
                                        <Label>Approved Routes</Label>
                                        <p className="text-xs text-muted-foreground">
                                            Route keys the resolver can return, mapped to known transfer destinations.
                                        </p>
                                    </div>
                                    <Button type="button" variant="outline" size="sm" onClick={addRoute}>
                                        <Plus className="h-4 w-4 mr-2" />
                                        Add Route
                                    </Button>
                                </div>

                                <div className="space-y-3">
                                    {approvedRoutes.map((route, index) => (
                                        <div key={index} className="grid gap-3 rounded-md border p-3">
                                            <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
                                                <div className="grid gap-2">
                                                    <Label>Route Key</Label>
                                                    <Input
                                                        value={route.key}
                                                        onChange={(e) =>
                                                            updateRoute(index, { key: e.target.value })
                                                        }
                                                        placeholder="referral_tx"
                                                    />
                                                </div>
                                                <div className="grid gap-2">
                                                    <Label>Destination</Label>
                                                    <Input
                                                        value={route.destination}
                                                        onChange={(e) =>
                                                            updateRoute(index, { destination: e.target.value })
                                                        }
                                                        placeholder="+1234567890"
                                                    />
                                                </div>
                                                <div className="flex items-end">
                                                    <Button
                                                        type="button"
                                                        variant="ghost"
                                                        size="icon"
                                                        onClick={() => removeRoute(index)}
                                                        aria-label="Remove route"
                                                    >
                                                        <Trash2 className="h-4 w-4" />
                                                    </Button>
                                                </div>
                                            </div>
                                            <div className="grid gap-3 md:grid-cols-[1fr_160px]">
                                                <div className="grid gap-2">
                                                    <Label>Message</Label>
                                                    <Input
                                                        value={route.message || ""}
                                                        onChange={(e) =>
                                                            updateRoute(index, { message: e.target.value })
                                                        }
                                                        placeholder="I’ll transfer you now."
                                                    />
                                                </div>
                                                <div className="grid gap-2">
                                                    <Label>Timeout</Label>
                                                    <Input
                                                        type="number"
                                                        value={route.timeout_seconds ?? 30}
                                                        min="5"
                                                        max="120"
                                                        onChange={(e) => {
                                                            const value = parseInt(e.target.value) || 30;
                                                            updateRoute(index, {
                                                                timeout_seconds: Math.min(Math.max(value, 5), 120),
                                                            });
                                                        }}
                                                    />
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                    {approvedRoutes.length === 0 && (
                                        <p className="text-xs text-muted-foreground">
                                            No approved routes configured.
                                        </p>
                                    )}
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            </CardContent>
        </Card>
    );
}
