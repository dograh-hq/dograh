'use client';

import { MessageSquare, Mic, MicOff } from 'lucide-react';
import { ReactNode } from 'react';

import { cn } from '@/lib/utils';

type CallStatus = 'ready' | 'live' | 'ended';

interface TranscriptContainerProps {
    title: string;
    status: CallStatus;
    children: ReactNode;
    messageCount?: number;
}

const STATUS_CONFIG = {
    ready: {
        icon: MicOff,
        label: 'Ready',
        className: 'bg-muted text-muted-foreground',
    },
    live: {
        icon: Mic,
        label: 'Live',
        className: 'bg-green-500/10 text-green-600 dark:text-green-400',
    },
    ended: {
        icon: MicOff,
        label: 'Ended',
        className: 'bg-muted text-muted-foreground',
    },
};

export function TranscriptContainer({
    title,
    status,
    children,
    messageCount
}: TranscriptContainerProps) {
    const statusConfig = STATUS_CONFIG[status];
    const StatusIcon = statusConfig.icon;

    return (
        <div className="w-full h-full flex flex-col bg-background border-l border-border">
            {/* Header */}
            <div className="px-4 py-3 border-b border-border shrink-0">
                <div className="flex items-center justify-center gap-2">
                    <MessageSquare className="h-4 w-4 text-muted-foreground shrink-0" />
                    <span className="font-medium text-sm whitespace-nowrap">{title}</span>
                    <div className={cn(
                        "flex items-center gap-1 text-xs px-2 py-0.5 rounded-full shrink-0",
                        statusConfig.className
                    )}>
                        <StatusIcon className="h-3 w-3" />
                        <span>{statusConfig.label}</span>
                    </div>
                </div>
            </div>

            {/* Content */}
            {children}

            {/* Footer with message count */}
            {messageCount !== undefined && messageCount > 0 && (
                <div className="px-4 py-2 border-t border-border text-xs text-muted-foreground shrink-0">
                    {messageCount} messages
                </div>
            )}
        </div>
    );
}
