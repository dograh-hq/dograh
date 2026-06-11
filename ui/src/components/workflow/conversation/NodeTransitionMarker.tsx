"use client";

import { GitBranch } from "lucide-react";

interface NodeTransitionMarkerProps {
    nodeName: string;
}

export function NodeTransitionMarker({ nodeName }: NodeTransitionMarkerProps) {
    return (
        <div className="flex items-center gap-2 py-2">
            <div className="h-px flex-1 bg-border" />
            <div className="inline-flex items-center gap-1.5 rounded-full border border-zinc-300 bg-zinc-100 px-3 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-800">
                <GitBranch className="h-3 w-3 text-zinc-500" />
                <span className="font-medium text-zinc-700 dark:text-zinc-300">{nodeName}</span>
            </div>
            <div className="h-px flex-1 bg-border" />
        </div>
    );
}
