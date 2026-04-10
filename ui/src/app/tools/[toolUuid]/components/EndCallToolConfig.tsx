"use client";

import { AlertCircle } from "lucide-react";

import type { RecordingResponseSchema } from "@/client/types.gen";
import { RecordingSelect } from "@/components/flow/TextOrAudioInput";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

import { type EndCallMessageType } from "../../config";

export interface EndCallToolConfigProps {
    name: string;
    onNameChange: (name: string) => void;
    description: string;
    onDescriptionChange: (description: string) => void;
    messageType: EndCallMessageType;
    onMessageTypeChange: (messageType: EndCallMessageType) => void;
    customMessage: string;
    onCustomMessageChange: (message: string) => void;
    audioRecordingId: string;
    onAudioRecordingIdChange: (id: string) => void;
    recordings?: RecordingResponseSchema[];
    endCallReason: boolean;
    onEndCallReasonChange: (enabled: boolean) => void;
    endCallReasonDescription: string;
    onEndCallReasonDescriptionChange: (description: string) => void;
}

export function EndCallToolConfig({
    name,
    onNameChange,
    description,
    onDescriptionChange,
    messageType,
    onMessageTypeChange,
    customMessage,
    onCustomMessageChange,
    audioRecordingId,
    onAudioRecordingIdChange,
    recordings = [],
    endCallReason,
    onEndCallReasonChange,
    endCallReasonDescription,
    onEndCallReasonDescriptionChange,
}: EndCallToolConfigProps) {
    return (
        <Card>
            <CardHeader>
                <CardTitle>End Call Configuration</CardTitle>
                <CardDescription>
                    Configure the behavior when the call ends
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
                        placeholder="e.g., End Call"
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
                        placeholder="When should the AI end the call?"
                        rows={3}
                    />
                </div>

                <div className="grid gap-2 pt-4 border-t">
                    <div className="flex items-center space-x-2">
                        <Switch
                            id="end-call-reason"
                            checked={endCallReason}
                            onCheckedChange={onEndCallReasonChange}
                        />
                        <Label htmlFor="end-call-reason">Capture End Call Reason</Label>
                    </div>
                    <Label className="text-xs text-muted-foreground">
                        When enabled, the AI will provide a reason for ending the call.
                        The reason will be set as the call disposition and added to call tags for analytics.
                    </Label>
                    {endCallReason && (
                        <div className="grid gap-2 pt-2">
                            <Label>Reason Description</Label>
                            <Label className="text-xs text-muted-foreground">
                                Instructions shown to the AI for what kind of reason to provide
                            </Label>
                            <Textarea
                                value={endCallReasonDescription}
                                onChange={(e) => onEndCallReasonDescriptionChange(e.target.value)}
                                placeholder="e.g., The reason for ending the call (e.g., 'voicemail_detected', 'issue_resolved', 'customer_requested')"
                                rows={2}
                            />
                        </div>
                    )}
                </div>

                <div className="grid gap-4 pt-4 border-t">
                    <Label>Goodbye Message</Label>
                    <Label className="text-xs text-muted-foreground">
                        Choose whether to play a message before disconnecting
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
                                    End the call immediately without any message
                                </p>
                            </div>
                        </label>
                        <div className="flex items-start space-x-3 p-3 border rounded-lg hover:bg-muted/50">
                            <RadioGroupItem value="custom" id="custom" className="mt-1" />
                            <label htmlFor="custom" className="flex-1 space-y-2 cursor-pointer">
                                <span className="font-medium">Custom Message</span>
                                <p className="text-xs text-muted-foreground">
                                    Play a custom message before disconnecting
                                </p>
                            </label>
                        </div>
                        {messageType === "custom" && (
                            <div className="pl-8 space-y-2">
                                <div className="flex items-start gap-2 rounded-md bg-amber-50 p-2 text-xs text-amber-700 border border-amber-200">
                                    <AlertCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                                    <span>This text is spoken as-is. For multilingual workflows, choose your phrasing carefully.</span>
                                </div>
                                <Textarea
                                    value={customMessage}
                                    onChange={(e) => onCustomMessageChange(e.target.value)}
                                    placeholder="e.g., Thank you for calling. Goodbye!"
                                    rows={2}
                                />
                            </div>
                        )}
                        <div className="flex items-start space-x-3 p-3 border rounded-lg hover:bg-muted/50">
                            <RadioGroupItem value="audio" id="audio" className="mt-1" />
                            <label htmlFor="audio" className="flex-1 space-y-2 cursor-pointer">
                                <span className="font-medium">Pre-recorded Audio</span>
                                <p className="text-xs text-muted-foreground">
                                    Play a pre-recorded audio file before disconnecting
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
            </CardContent>
        </Card>
    );
}
