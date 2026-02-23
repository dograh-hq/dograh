"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import { useState, useEffect } from "react";

import { type EndCallMessageType } from "../../config";

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
    timeout?: number;  // Make optional to match API type
    onTimeoutChange: (timeout: number) => void;
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
    timeout,
    onTimeoutChange,
}: TransferCallToolConfigProps) {
    const [sipMode, setSipMode] = useState(() => /^(PJSIP|SIP)\//i.test(destination));

    // Validation patterns
    const isValidPhoneNumber = (phone: string): boolean => {
        const e164Pattern = /^\+[1-9]\d{1,14}$/;
        return e164Pattern.test(phone);
    };

    const isValidSipEndpoint = (endpoint: string): boolean => {
        const sipPattern = /^(PJSIP|SIP)\/[\w\-\.@]+$/i;
        return sipPattern.test(endpoint);
    };

    const getValidationError = (): string | null => {
        if (!destination) return null;
        
        if (sipMode) {
            return isValidSipEndpoint(destination) 
                ? null 
                : "Please enter a valid SIP endpoint (e.g., PJSIP/1234 or SIP/extension@domain.com)";
        } else {
            return isValidPhoneNumber(destination) 
                ? null 
                : "Please enter a valid phone number in E.164 format (e.g., +1234567890)";
        }
    };

    const destinationError = getValidationError();

    const handleSipModeToggle = () => {
        setSipMode(!sipMode);
        onDestinationChange(""); // Clear destination when switching modes
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
                    <Label className="text-xs text-muted-foreground">
                        {sipMode 
                            ? "SIP endpoint to transfer the call to (e.g., PJSIP/1234 or SIP/extension@domain.com)"
                            : "Phone number to transfer the call to (E.164 format with country code)"
                        }
                    </Label>
                    <Input
                        value={destination}
                        onChange={(e) => onDestinationChange(e.target.value)}
                        placeholder={sipMode ? "PJSIP/1234 or SIP/extension@domain.com" : "+1234567890"}
                        className={destinationError ? "border-red-500 focus:border-red-500" : ""}
                    />
                    {destinationError && (
                        <Label className="text-xs text-red-500">
                            {destinationError}
                        </Label>
                    )}
                    <button
                        type="button"
                        className="text-xs text-muted-foreground hover:text-foreground underline w-fit"
                        onClick={handleSipModeToggle}
                    >
                        {sipMode ? "Use phone number instead" : "Use SIP endpoint instead"}
                    </button>
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
                            <div className="pl-8">
                                <Textarea
                                    value={customMessage}
                                    onChange={(e) => onCustomMessageChange(e.target.value)}
                                    placeholder="e.g., Please hold while I transfer your call."
                                    rows={2}
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
            </CardContent>
        </Card>
    );
}
