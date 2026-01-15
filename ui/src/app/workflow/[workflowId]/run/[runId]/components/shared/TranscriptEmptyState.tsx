'use client';

import { MessageSquare } from 'lucide-react';

interface TranscriptEmptyStateProps {
    title: string;
    subtitle: string;
}

export function TranscriptEmptyState({ title, subtitle }: TranscriptEmptyStateProps) {
    return (
        <div className="flex flex-col items-center justify-center h-full text-muted-foreground text-sm">
            <MessageSquare className="h-10 w-10 mb-4 opacity-30" />
            <p className="font-medium">{title}</p>
            <p className="text-xs mt-1 text-center px-4">
                {subtitle}
            </p>
        </div>
    );
}
