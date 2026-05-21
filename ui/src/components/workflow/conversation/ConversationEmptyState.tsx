"use client";

import { MessageSquare } from "lucide-react";

import type { ConversationEmptyStateData } from "./types";

export function ConversationEmptyState({ title, subtitle }: ConversationEmptyStateData) {
    return (
        <div className="flex h-full flex-col items-center justify-center text-sm text-muted-foreground">
            <MessageSquare className="mb-4 h-10 w-10 opacity-30" />
            <p className="font-medium">{title}</p>
            <p className="mt-1 px-4 text-center text-xs">{subtitle}</p>
        </div>
    );
}
