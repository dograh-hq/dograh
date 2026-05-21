"use client";

import { GitBranch } from "lucide-react";

interface NodeTransitionMarkerProps {
    nodeName: string;
}

export function NodeTransitionMarker({ nodeName }: NodeTransitionMarkerProps) {
    return (
        <div className="flex items-center gap-2 py-2">
            <div className="h-px flex-1 bg-border" />
            <div className="inline-flex items-center gap-1.5 rounded-full border border-blue-500/20 bg-blue-500/10 px-3 py-1 text-xs">
                <GitBranch className="h-3 w-3 text-blue-500" />
                <span className="font-medium text-blue-700 dark:text-blue-400">{nodeName}</span>
            </div>
            <div className="h-px flex-1 bg-border" />
        </div>
    );
}
