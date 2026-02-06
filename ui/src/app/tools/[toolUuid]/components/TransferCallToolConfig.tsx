"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export interface TransferCallToolConfigProps {
    name: string;
    onNameChange: (name: string) => void;
    description: string;
    onDescriptionChange: (description: string) => void;
    transferNumber: string;
    onTransferNumberChange: (number: string) => void;
    transferMessage: string;
    onTransferMessageChange: (message: string) => void;
}

export function TransferCallToolConfig({
    name,
    onNameChange,
    description,
    onDescriptionChange,
    transferNumber,
    onTransferNumberChange,
    transferMessage,
    onTransferMessageChange,
}: TransferCallToolConfigProps) {
    return (
        <Card>
            <CardHeader>
                <CardTitle>Transfer Call Configuration</CardTitle>
                <CardDescription>
                    Configure call transfer behavior
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
                    <div className="grid gap-2">
                        <Label>Transfer Number</Label>
                        <Label className="text-xs text-muted-foreground">
                            The phone number to transfer the call to
                        </Label>
                        <Input
                            value={transferNumber}
                            onChange={(e) => onTransferNumberChange(e.target.value)}
                            placeholder="e.g., +14155551234"
                        />
                    </div>

                    <div className="grid gap-2">
                        <Label>Transfer Message</Label>
                        <Label className="text-xs text-muted-foreground">
                            Optional message to play before transferring
                        </Label>
                        <Textarea
                            value={transferMessage}
                            onChange={(e) => onTransferMessageChange(e.target.value)}
                            placeholder="e.g., Please hold while I transfer your call."
                            rows={2}
                        />
                    </div>
                </div>
            </CardContent>
        </Card>
    );
}
