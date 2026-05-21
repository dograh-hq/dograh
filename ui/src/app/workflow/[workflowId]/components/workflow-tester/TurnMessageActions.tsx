"use client";

import { Loader2, Pencil, RotateCcw } from "lucide-react";

import { cn } from "@/lib/utils";

interface TurnMessageActionsProps {
    disabled: boolean;
    editing: boolean;
    rewinding: boolean;
    rerunningEdit: boolean;
    onRewind: () => void;
    onEdit: () => void;
}

export function TurnMessageActions({
    disabled,
    editing,
    rewinding,
    rerunningEdit,
    onRewind,
    onEdit,
}: TurnMessageActionsProps) {
    return (
        <>
            <button
                type="button"
                onClick={onRewind}
                disabled={disabled}
                aria-label="Rerun this turn"
                title="Rerun this turn"
                className="inline-flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
            >
                {rewinding ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                    <RotateCcw className="h-3.5 w-3.5" />
                )}
            </button>
            <button
                type="button"
                onClick={onEdit}
                disabled={disabled}
                aria-label="Edit and rerun this turn"
                title="Edit and rerun this turn"
                className={cn(
                    "inline-flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50",
                    editing && "bg-muted text-foreground",
                )}
            >
                {rerunningEdit ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                    <Pencil className="h-3.5 w-3.5" />
                )}
            </button>
        </>
    );
}
