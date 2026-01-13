"use client";

import { Loader2, MessageSquare, Mic, MicOff, Wrench } from "lucide-react";
import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils";

import { FeedbackMessage } from "../hooks/useWebSocketRTC";

interface RealtimeFeedbackPanelProps {
    messages: FeedbackMessage[];
    isVisible: boolean;
    isCallActive: boolean;
    isCallCompleted: boolean;
}

const MessageItem = ({ msg }: { msg: FeedbackMessage }) => {
    // Function call message - centered
    if (msg.type === 'function-call') {
        return (
            <div className="flex justify-center">
                <div className="px-3 py-1.5 rounded-full text-xs bg-amber-500/10 border border-amber-500/20 inline-flex items-center gap-2">
                    {msg.status === 'running' ? (
                        <Loader2 className="h-3 w-3 animate-spin text-amber-500" />
                    ) : (
                        <Wrench className="h-3 w-3 text-amber-500" />
                    )}
                    <span className="font-mono text-amber-700 dark:text-amber-400">
                        {msg.functionName}()
                    </span>
                    {msg.status === 'completed' && (
                        <span className="text-muted-foreground">✓</span>
                    )}
                </div>
            </div>
        );
    }

    const isUser = msg.type === 'user-transcription';

    // User messages on right, bot messages on left
    return (
        <div className={cn(
            "flex",
            isUser ? "justify-end" : "justify-start"
        )}>
            <div
                className={cn(
                    "max-w-[85%] px-3 py-2 rounded-2xl text-sm",
                    isUser
                        ? "bg-primary text-primary-foreground rounded-br-md"
                        : "bg-muted rounded-bl-md",
                    !msg.final && "opacity-70"
                )}
            >
                <div className="whitespace-pre-wrap leading-relaxed">{msg.text}</div>
                {!msg.final && (
                    <div className={cn(
                        "text-[10px] mt-1 italic",
                        isUser ? "text-primary-foreground/70" : "text-muted-foreground"
                    )}>
                        speaking...
                    </div>
                )}
            </div>
        </div>
    );
};

export const RealtimeFeedbackPanel = ({
    messages,
    isVisible,
    isCallActive,
    isCallCompleted
}: RealtimeFeedbackPanelProps) => {
    const scrollRef = useRef<HTMLDivElement>(null);

    // Auto-scroll to bottom when new messages arrive
    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [messages]);

    if (!isVisible) return null;

    return (
        <div className="w-full h-full flex flex-col bg-background border-l border-border">
            {/* Header */}
            <div className="px-4 py-3 border-b border-border shrink-0">
                <div className="flex items-center justify-center gap-2">
                    <MessageSquare className="h-4 w-4 text-muted-foreground shrink-0" />
                    <span className="font-medium text-sm whitespace-nowrap">Live Transcript</span>
                    <div className={cn(
                        "flex items-center gap-1 text-xs px-2 py-0.5 rounded-full shrink-0",
                        isCallActive
                            ? "bg-green-500/10 text-green-600 dark:text-green-400"
                            : isCallCompleted
                                ? "bg-muted text-muted-foreground"
                                : "bg-muted text-muted-foreground"
                    )}>
                        {isCallActive ? (
                            <>
                                <Mic className="h-3 w-3" />
                                <span>Live</span>
                            </>
                        ) : isCallCompleted ? (
                            <>
                                <MicOff className="h-3 w-3" />
                                <span>Ended</span>
                            </>
                        ) : (
                            <>
                                <MicOff className="h-3 w-3" />
                                <span>Ready</span>
                            </>
                        )}
                    </div>
                </div>
            </div>

            {/* Messages */}
            <div ref={scrollRef} className="flex-1 overflow-y-auto">
                {messages.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-full text-muted-foreground text-sm">
                        <MessageSquare className="h-10 w-10 mb-4 opacity-30" />
                        <p className="font-medium">No messages yet</p>
                        <p className="text-xs mt-1 text-center px-4">
                            {isCallActive
                                ? "Start speaking to see the transcript"
                                : "Start the call to begin the conversation"
                            }
                        </p>
                    </div>
                ) : (
                    <div className="space-y-3 p-4">
                        {messages.map((msg) => (
                            <MessageItem key={msg.id} msg={msg} />
                        ))}
                    </div>
                )}
            </div>

            {/* Footer with message count */}
            {messages.length > 0 && (
                <div className="px-4 py-2 border-t border-border text-xs text-muted-foreground shrink-0">
                    {messages.filter(m => m.type !== 'function-call').length} messages
                </div>
            )}
        </div>
    );
};
