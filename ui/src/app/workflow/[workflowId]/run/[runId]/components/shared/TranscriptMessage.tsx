'use client';

import { Brain, GitBranch, Wrench } from 'lucide-react';

import { cn } from '@/lib/utils';

export interface TranscriptMessageData {
    id: string;
    type: 'user-transcription' | 'bot-text' | 'function-call' | 'node-transition' | 'ttfb-metric';
    text: string;
    final?: boolean;
    functionName?: string;
    nodeName?: string;
    ttfbSeconds?: number;
}

interface TranscriptMessageProps {
    message: TranscriptMessageData;
    nextMessage?: TranscriptMessageData;
}

export function TranscriptMessage({ message, nextMessage }: TranscriptMessageProps) {
    // Node transition - show as section divider
    if (message.type === 'node-transition') {
        return (
            <div className="flex items-center gap-2 py-2">
                <div className="flex-1 h-px bg-border"></div>
                <div className="px-2 py-1 rounded-md text-xs bg-blue-500/10 border border-blue-500/20 inline-flex items-center gap-1.5">
                    <GitBranch className="h-3 w-3 text-blue-500" />
                    <span className="font-medium text-blue-700 dark:text-blue-400">
                        {message.nodeName}
                    </span>
                </div>
                <div className="flex-1 h-px bg-border"></div>
            </div>
        );
    }

    // TTFB metric - don't render standalone, it'll be shown with bot messages and function calls
    if (message.type === 'ttfb-metric') {
        return null;
    }

    // Function call message - centered with TTFB if present
    if (message.type === 'function-call') {
        const ttfbMetric = nextMessage?.type === 'ttfb-metric' ? nextMessage : null;
        return (
            <div className="flex flex-col items-center gap-1">
                {/* Show TTFB metric above function call */}
                {ttfbMetric && ttfbMetric.ttfbSeconds !== undefined && (
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <Brain className="h-3 w-3" />
                        <span className="font-medium">Reasoning Delay:</span>
                        <span>{(ttfbMetric.ttfbSeconds * 1000).toFixed(0)}ms</span>
                    </div>
                )}
                <div className="px-3 py-1.5 rounded-full text-xs bg-amber-500/10 border border-amber-500/20 inline-flex items-center gap-2">
                    <Wrench className="h-3 w-3 text-amber-500" />
                    <span className="font-mono text-amber-700 dark:text-amber-400">
                        {message.functionName}()
                    </span>
                </div>
            </div>
        );
    }

    const isUser = message.type === 'user-transcription';
    const isBot = message.type === 'bot-text';

    // Check if next message is a TTFB metric (for bot messages)
    const ttfbMetric = isBot && nextMessage?.type === 'ttfb-metric' ? nextMessage : null;

    // User messages on right, bot messages on left
    return (
        <div className={cn(
            "flex",
            isUser ? "justify-end" : "justify-start"
        )}>
            <div className="flex flex-col gap-1 max-w-[85%]">
                {/* Show TTFB metric above bot messages */}
                {ttfbMetric && ttfbMetric.ttfbSeconds !== undefined && (
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground px-1">
                        <Brain className="h-3 w-3" />
                        <span className="font-medium">Reasoning Delay:</span>
                        <span>{(ttfbMetric.ttfbSeconds * 1000).toFixed(0)}ms</span>
                    </div>
                )}
                <div
                    className={cn(
                        "px-3 py-2 rounded-2xl text-sm",
                        isUser
                            ? "bg-primary text-primary-foreground rounded-br-md"
                            : "bg-muted rounded-bl-md",
                        !message.final && "opacity-70"
                    )}
                >
                    <div className="whitespace-pre-wrap leading-relaxed">{message.text}</div>
                    {!message.final && (
                        <div className={cn(
                            "text-[10px] mt-1 italic",
                            isUser ? "text-primary-foreground/70" : "text-muted-foreground"
                        )}>
                            speaking...
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
