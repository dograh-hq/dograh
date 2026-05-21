"use client";

import type { ReactNode } from "react";
import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils";

import { ConversationEmptyState } from "./ConversationEmptyState";
import { ConversationItemView } from "./ConversationItemView";
import type { ConversationEmptyStateData, ConversationItem } from "./types";

interface ConversationTimelineProps {
    items: ConversationItem[];
    autoScroll?: boolean;
    scrollBehavior?: ScrollBehavior;
    emptyState: ConversationEmptyStateData;
    pendingIndicator?: ReactNode;
    renderItemActions?: (item: ConversationItem) => ReactNode;
    className?: string;
}

export function ConversationTimeline({
    items,
    autoScroll = false,
    scrollBehavior = "auto",
    emptyState,
    pendingIndicator,
    renderItemActions,
    className,
}: ConversationTimelineProps) {
    const scrollContainerRef = useRef<HTMLDivElement | null>(null);
    const scrollEndRef = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        if (!autoScroll) {
            return;
        }
        scrollEndRef.current?.scrollIntoView({ behavior: scrollBehavior, block: "end" });
    }, [autoScroll, items, pendingIndicator, scrollBehavior]);

    return (
        <div ref={scrollContainerRef} className={cn("flex-1 overflow-y-auto", className)}>
            {items.length === 0 && !pendingIndicator ? (
                <ConversationEmptyState title={emptyState.title} subtitle={emptyState.subtitle} />
            ) : (
                <div className="space-y-3 p-4">
                    {items.map((item) => (
                        <ConversationItemView
                            key={item.id}
                            item={item}
                            actions={renderItemActions?.(item)}
                        />
                    ))}
                    {pendingIndicator}
                    <div ref={scrollEndRef} />
                </div>
            )}
        </div>
    );
}
