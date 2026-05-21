"use client";

import type { ReactNode } from "react";

import { MessageBubble } from "./MessageBubble";
import { NodeTransitionMarker } from "./NodeTransitionMarker";
import { NoticeCard } from "./NoticeCard";
import { ToolCallCard } from "./ToolCallCard";
import type { ConversationItem } from "./types";

interface ConversationItemViewProps {
    item: ConversationItem;
    actions?: ReactNode;
}

export function ConversationItemView({ item, actions }: ConversationItemViewProps) {
    if (item.kind === "message") {
        const isUser = item.role === "user";
        const bubble = (
            <MessageBubble
                role={item.role}
                text={item.text}
                final={item.final}
                tone={item.tone}
                reasoningDurationMs={item.reasoningDurationMs}
                containerClassName={isUser && actions ? "min-w-0 flex-1 justify-end" : undefined}
            />
        );

        if (isUser && actions) {
            return (
                <div className="group flex w-full justify-end">
                    <div className="flex w-full items-end justify-end gap-2">
                        <div className="flex shrink-0 items-center gap-1 rounded-lg border border-border/60 bg-background/95 px-1 py-0.5 shadow-sm">
                            {actions}
                        </div>
                        {bubble}
                    </div>
                </div>
            );
        }

        return (
            <div className="group space-y-1">
                {bubble}
                {actions ? (
                    <div className="flex h-5 items-center justify-end gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                        {actions}
                    </div>
                ) : null}
            </div>
        );
    }

    if (item.kind === "tool-call") {
        return (
            <ToolCallCard
                functionName={item.functionName}
                status={item.status}
                argumentsValue={item.arguments}
                resultValue={item.result}
                reasoningDurationMs={item.reasoningDurationMs}
            />
        );
    }

    if (item.kind === "node-transition") {
        return <NodeTransitionMarker nodeName={item.nodeName} />;
    }

    return (
        <NoticeCard
            tone={item.tone}
            title={item.title}
            text={item.text}
            linkHref={item.linkHref}
            linkLabel={item.linkLabel}
        />
    );
}
