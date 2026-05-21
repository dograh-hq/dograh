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
        return (
            <div className="group space-y-1">
                <MessageBubble
                    role={item.role}
                    text={item.text}
                    final={item.final}
                    tone={item.tone}
                    reasoningDurationMs={item.reasoningDurationMs}
                />
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
