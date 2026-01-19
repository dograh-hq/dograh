"use client";

import { useEffect, useRef } from "react";

import { ProcessedMessage } from "../utils/processTranscriptEvents";
import { TranscriptContainer } from "./shared/TranscriptContainer";
import { TranscriptEmptyState } from "./shared/TranscriptEmptyState";
import { TranscriptMessage, TranscriptMessageData } from "./shared/TranscriptMessage";

interface UnifiedTranscriptProps {
    messages: ProcessedMessage[];
    status: 'ready' | 'live' | 'ended';
    title?: string;
    autoScroll?: boolean;
    emptyState?: {
        title: string;
        subtitle: string;
    };
}

export const UnifiedTranscript = ({
    messages,
    status,
    title,
    autoScroll = false,
    emptyState
}: UnifiedTranscriptProps) => {
    const scrollRef = useRef<HTMLDivElement>(null);

    // Auto-scroll to bottom when new messages arrive (for live mode)
    useEffect(() => {
        if (autoScroll && scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [messages, autoScroll]);

    // Calculate message count (exclude system messages like function calls, node transitions, TTFB)
    const messageCount = messages.filter(
        m => m.type === 'user-transcription' || m.type === 'bot-text'
    ).length;

    // Convert ProcessedMessage to TranscriptMessageData
    const transcriptMessages: TranscriptMessageData[] = messages.map(msg => ({
        id: msg.id,
        type: msg.type,
        text: msg.text,
        final: msg.final,
        functionName: msg.functionName,
        status: msg.status,
        nodeName: msg.nodeName,
        ttfbSeconds: msg.ttfbSeconds,
    }));

    // Default empty state
    const defaultEmptyState = {
        title: status === 'live' ? "No messages yet" : "No conversation recorded",
        subtitle: status === 'live'
            ? "Start speaking to see the transcript"
            : "Real-time feedback events were not captured"
    };

    const emptyStateToShow = emptyState || defaultEmptyState;

    return (
        <TranscriptContainer
            title={title || (status === 'live' ? 'Live Transcript' : 'Call Transcript')}
            status={status}
            messageCount={messageCount > 0 ? messageCount : undefined}
        >
            <div ref={scrollRef} className="flex-1 overflow-y-auto">
                {messages.length === 0 ? (
                    <TranscriptEmptyState
                        title={emptyStateToShow.title}
                        subtitle={emptyStateToShow.subtitle}
                    />
                ) : (
                    <div className="space-y-3 p-4">
                        {transcriptMessages.map((msg, index) => {
                            // Skip standalone TTFB metrics (they're rendered inline with bot text)
                            if (msg.type === 'ttfb-metric') {
                                return null;
                            }
                            return (
                                <TranscriptMessage
                                    key={msg.id}
                                    message={msg}
                                    nextMessage={transcriptMessages[index + 1]}
                                />
                            );
                        })}
                    </div>
                )}
            </div>
        </TranscriptContainer>
    );
};
