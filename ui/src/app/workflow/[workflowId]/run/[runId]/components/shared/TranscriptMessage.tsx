'use client';

import { AlertTriangle, Brain, ExternalLink, GitBranch, MicOff, Wrench } from 'lucide-react';

import { cn } from '@/lib/utils';

export interface TranscriptMessageData {
    id: string;
    type: 'user-transcription' | 'bot-text' | 'function-call' | 'node-transition' | 'ttfb-metric' | 'pipeline-error' | 'interrupt-warning';
    text: string;
    final?: boolean;
    functionName?: string;
    nodeName?: string;
    allowInterrupt?: boolean;
    ttfbSeconds?: number;
    fatal?: boolean;
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

    // Interrupt warning - show as an amber alert (one-time)
    if (message.type === 'interrupt-warning') {
        return (
            <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/20">
                <MicOff className="h-4 w-4 text-amber-500 mt-0.5 shrink-0" />
                <div className="flex-1 min-w-0">
                    <div className="text-xs font-medium text-amber-700 dark:text-amber-400">
                        Interruption Disabled
                    </div>
                    <div className="text-sm text-amber-600 dark:text-amber-300 mt-0.5">
                        {message.text}
                    </div>
                    <a
                        href="https://docs.dograh.com/configurations/interruption"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400 hover:underline mt-1"
                    >
                        Learn more <ExternalLink className="h-3 w-3" />
                    </a>
                </div>
            </div>
        );
    }

    // Pipeline error - show as a red alert
    if (message.type === 'pipeline-error') {
        return (
            <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20">
                <AlertTriangle className="h-4 w-4 text-red-500 mt-0.5 shrink-0" />
                <div className="flex-1 min-w-0">
                    <div className="text-xs font-medium text-red-700 dark:text-red-400">
                        {message.fatal ? 'Fatal Pipeline Error' : 'Pipeline Error'}
                    </div>
                    <div className="text-sm text-red-600 dark:text-red-300 mt-0.5 break-words">
                        {message.text}
                    </div>
                </div>
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
