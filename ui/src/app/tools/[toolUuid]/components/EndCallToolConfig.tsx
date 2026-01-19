"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
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
                            <div className="pl-8">
                                <Textarea
                                    value={customMessage}
                                    onChange={(e) => onCustomMessageChange(e.target.value)}
                                    placeholder="e.g., Thank you for calling. Goodbye!"
                                    rows={2}
                                />
                            </div>
                        )}
                    </RadioGroup>
                </div>
            </CardContent>
        </Card>
    );
}
