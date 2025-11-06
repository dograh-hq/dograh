"use client";

import { useRouter } from "next/navigation";

import { cn } from "@/lib/utils";

interface WorkflowTabsProps {
    workflowId: number;
    currentTab: 'editor' | 'executions';
}

export const WorkflowTabs = ({ workflowId, currentTab }: WorkflowTabsProps) => {
    const router = useRouter();

    const handleTabChange = (tab: 'editor' | 'executions') => {
        router.push(`/workflow/${workflowId}?tab=${tab}`, { scroll: false });
    };

    return (
        <div className="flex gap-2">
            <button
                onClick={() => handleTabChange('editor')}
                className={cn(
                    "px-6 py-2.5 text-sm font-medium transition-all relative cursor-pointer rounded-md",
                    currentTab === 'editor'
                        ? "text-white bg-[#3d4451]"
                        : "text-gray-300 hover:text-white hover:bg-[#343842]"
                )}
            >
                Editor
            </button>
            <button
                onClick={() => handleTabChange('executions')}
                className={cn(
                    "px-6 py-2.5 text-sm font-medium transition-all relative cursor-pointer rounded-md",
                    currentTab === 'executions'
                        ? "text-white bg-[#3d4451]"
                        : "text-gray-300 hover:text-white hover:bg-[#343842]"
                )}
            >
                Executions
            </button>
        </div>
    );
};
