"use client";

import { AlertCircle, MessageSquareText } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function DisabledNotice({ reason }: { reason: string }) {
    return (
        <div className="rounded-lg border border-amber-200/80 bg-amber-50/80 px-3 py-2.5 text-sm text-amber-900 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200">
            <div className="flex items-start gap-3">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                <div className="space-y-0.5">
                    <p className="font-medium">Testing is paused</p>
                    <p className="text-amber-800/90 dark:text-amber-300">{reason}</p>
                </div>
            </div>
        </div>
    );
}

export function EmptyState({
    icon,
    title,
    description,
    action,
}: {
    icon: ReactNode;
    title: string;
    description: string;
    action?: ReactNode;
}) {
    return (
        <div className="flex flex-1 flex-col justify-center rounded-xl border border-border/70 bg-background px-5 py-6 text-left">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                {icon}
            </div>
            <div className="mt-4 space-y-1.5">
                <h3 className="text-sm font-semibold text-foreground">{title}</h3>
                <p className="text-sm leading-6 text-muted-foreground">{description}</p>
            </div>
            {action ? <div className="mt-5">{action}</div> : null}
        </div>
    );
}

export function ChatModeToggle({
    value,
    onChange,
}: {
    value: "manual" | "simulated";
    onChange: (next: "manual" | "simulated") => void;
}) {
    const options: Array<{ id: "manual" | "simulated"; label: string }> = [
        { id: "manual", label: "Manual" },
        { id: "simulated", label: "Simulated" },
    ];

    return (
        <div className="inline-flex items-center gap-0.5 rounded-md border border-border/70 bg-muted/40 p-0.5">
            {options.map((option) => {
                const active = option.id === value;
                return (
                    <button
                        key={option.id}
                        type="button"
                        onClick={() => onChange(option.id)}
                        className={cn(
                            "rounded-[5px] px-2.5 py-1 text-xs font-medium transition",
                            active
                                ? "bg-background text-foreground shadow-xs"
                                : "text-muted-foreground hover:text-foreground",
                        )}
                    >
                        {option.label}
                    </button>
                );
            })}
        </div>
    );
}

export function TypingIndicator() {
    return (
        <div className="flex justify-start">
            <div className="rounded-2xl rounded-bl-md bg-muted px-3.5 py-3">
                <div className="flex items-center gap-1">
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:-0.3s]" />
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:-0.15s]" />
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/60" />
                </div>
            </div>
        </div>
    );
}

export function ManualChatEmptyState({
    disabled,
    ready,
    onStart,
}: {
    disabled: boolean;
    ready: boolean;
    onStart: () => void;
}) {
    return (
        <EmptyState
            icon={<MessageSquareText className="h-7 w-7" />}
            title="Chat with this agent"
            description="Test the agent over a text conversation. Send messages and see how it responds, with tool calls, transitions, and rewind support."
            action={
                <Button onClick={onStart} disabled={disabled || !ready}>
                    <MessageSquareText className="h-4 w-4" />
                    Start Test
                </Button>
            }
        />
    );
}
