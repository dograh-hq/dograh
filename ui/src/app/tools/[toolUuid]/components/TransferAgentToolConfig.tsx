"use client";

import type { TransferAgentConfig } from "@/client/types.gen";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

export type TransferGreetingMode = NonNullable<TransferAgentConfig["greeting_mode"]>;

export interface TransferAgentWorkflowOption {
    id: number;
    name: string;
}

/** How the destination agent opens, in the order the select offers them. */
const GREETING_MODES: { value: TransferGreetingMode; label: string; hint: string }[] = [
    {
        value: "start_greeting",
        label: "Play its greeting, then wait",
        hint: "The destination plays its Start Call greeting and waits for the caller to speak.",
    },
    {
        value: "continue",
        label: "Continue the conversation",
        hint: "No greeting. The destination opens with a reply based on the handover note.",
    },
    {
        value: "introduce_and_continue",
        label: "Introduce itself, then continue",
        hint: "The destination says the introduction below in its own voice, then replies based on the handover note.",
    },
];

export interface TransferAgentToolConfigProps {
    name: string;
    onNameChange: (name: string) => void;
    description: string;
    onDescriptionChange: (description: string) => void;
    /** The agent this tool transfers to. */
    workflowId: string;
    onWorkflowIdChange: (workflowId: string) => void;
    /** Agents in this organization, offered as the destination. */
    workflows: TransferAgentWorkflowOption[];
    workflowsLoading?: boolean;
    message: string;
    onMessageChange: (message: string) => void;
    /** How the destination agent opens once it has the call. */
    greetingMode: TransferGreetingMode;
    onGreetingModeChange: (greetingMode: TransferGreetingMode) => void;
    /** What the destination says as it takes over; used by "introduce_and_continue". */
    introduction: string;
    onIntroductionChange: (introduction: string) => void;
}

export function TransferAgentToolConfig({
    name,
    onNameChange,
    description,
    onDescriptionChange,
    workflowId,
    onWorkflowIdChange,
    workflows,
    workflowsLoading = false,
    message,
    onMessageChange,
    greetingMode,
    onGreetingModeChange,
    introduction,
    onIntroductionChange,
}: TransferAgentToolConfigProps) {
    const selectedMode = GREETING_MODES.find((mode) => mode.value === greetingMode);
    return (
        <Card>
            <CardHeader>
                <CardTitle>Transfer To Agent Configuration</CardTitle>
                <CardDescription>
                    Hands the live call to another agent. The caller stays connected,
                    hears a ringer while the next agent is prepared, and the
                    conversation so far is passed on as a handover note.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
                <div className="grid gap-2">
                    <Label htmlFor="transfer-agent-name">Tool Name</Label>
                    <Label className="text-xs text-muted-foreground">
                        Also becomes the function name the agent calls. Name it after
                        the destination, e.g. &ldquo;Transfer to Billing&rdquo;.
                    </Label>
                    <Input
                        id="transfer-agent-name"
                        value={name}
                        onChange={(e) => onNameChange(e.target.value)}
                        placeholder="e.g., Transfer to Billing"
                    />
                </div>

                <div className="grid gap-2">
                    <Label htmlFor="transfer-agent-description">Description</Label>
                    <Label className="text-xs text-muted-foreground">
                        This is what the agent decides on. To offer more than one
                        destination, add a second transfer tool with its own
                        description.
                    </Label>
                    <Textarea
                        id="transfer-agent-description"
                        value={description}
                        onChange={(e) => onDescriptionChange(e.target.value)}
                        placeholder="Use when the caller asks about an invoice, a payment or their balance"
                        rows={3}
                    />
                </div>

                <div className="grid gap-2">
                    <Label htmlFor="transfer-agent-workflow">Transfer to agent</Label>
                    <Select value={workflowId} onValueChange={onWorkflowIdChange}>
                        <SelectTrigger id="transfer-agent-workflow">
                            <SelectValue
                                placeholder={
                                    workflowsLoading ? "Loading agents…" : "Select an agent"
                                }
                            />
                        </SelectTrigger>
                        <SelectContent>
                            {workflows.map((workflow) => (
                                <SelectItem key={workflow.id} value={String(workflow.id)}>
                                    {workflow.name}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>

                <div className="grid gap-2">
                    <Label htmlFor="transfer-agent-message">Handover message</Label>
                    <Label className="text-xs text-muted-foreground">
                        Spoken in the current agent&apos;s own voice, and waited for
                        before the caller is handed over. Leave empty to hand over
                        without saying anything.
                    </Label>
                    <Input
                        id="transfer-agent-message"
                        maxLength={500}
                        value={message}
                        onChange={(e) => onMessageChange(e.target.value)}
                        placeholder="Let me connect you with the right person. One moment please."
                    />
                </div>

                <div className="grid gap-2">
                    <Label htmlFor="transfer-agent-greeting-mode">
                        When the destination agent picks up
                    </Label>
                    <Select
                        value={greetingMode}
                        onValueChange={(value) =>
                            onGreetingModeChange(value as TransferGreetingMode)
                        }
                    >
                        <SelectTrigger id="transfer-agent-greeting-mode">
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            {GREETING_MODES.map((mode) => (
                                <SelectItem key={mode.value} value={mode.value}>
                                    {mode.label}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    {selectedMode && (
                        <p className="text-xs text-muted-foreground">{selectedMode.hint}</p>
                    )}
                </div>

                {greetingMode === "introduce_and_continue" && (
                    <div className="grid gap-2">
                        <Label htmlFor="transfer-agent-introduction">Introduction</Label>
                        <Label className="text-xs text-muted-foreground">
                            Spoken in the destination agent&apos;s own voice before its
                            first reply. Write it in the language that agent speaks.
                            Supports template variables.
                        </Label>
                        <Textarea
                            id="transfer-agent-introduction"
                            maxLength={500}
                            value={introduction}
                            onChange={(e) => onIntroductionChange(e.target.value)}
                            placeholder="Hi, this is Billing. I have your details from my colleague, so let's carry on."
                            rows={2}
                        />
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
