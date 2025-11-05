"use client";

import { useRouter } from "next/navigation";

import { cn } from "@/lib/utils";

interface WorkflowTabsProps {
    workflowId: number;
    currentTab: 'editor' | 'executions';
}

export const WorkflowTabs = ({ workflowId, currentTab }: WorkflowTabsProps) => {
    const router = useRouter();

    return (
        <div className="flex gap-1">
            <button
                onClick={() => router.push(`/workflow/${workflowId}`)}
                className={cn(
                    "px-4 py-2 text-sm font-medium transition-colors relative cursor-pointer",
                    currentTab === 'editor'
                        ? "text-gray-900"
                        : "text-gray-500 hover:text-gray-700"
                )}
            >
                Editor
                {currentTab === 'editor' && (
                    <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-blue-600" />
                )}
            </button>
            <button
                onClick={() => router.push(`/workflow/${workflowId}/runs`)}
                className={cn(
                    "px-4 py-2 text-sm font-medium transition-colors relative cursor-pointer",
                    currentTab === 'executions'
                        ? "text-gray-900"
                        : "text-gray-500 hover:text-gray-700"
                )}
            >
                Executions
                {currentTab === 'executions' && (
                    <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-blue-600" />
                )}
            </button>
        </div>
    );
};
