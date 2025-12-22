"use client";

import { ReactFlowInstance } from "@xyflow/react";
import { AlertCircle, ArrowLeft, ChevronDown, Download, History, LoaderCircle, MoreVertical, Phone } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { WorkflowError } from "@/client/types.gen";
import { FlowEdge, FlowNode } from "@/components/flow/types";
import { Button } from "@/components/ui/button";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from "@/components/ui/popover";
import { WORKFLOW_RUN_MODES } from "@/constants/workflowRunModes";

interface WorkflowEditorHeaderProps {
    workflowName: string;
    isDirty: boolean;
    workflowValidationErrors: WorkflowError[];
    rfInstance: React.RefObject<ReactFlowInstance<FlowNode, FlowEdge> | null>;
    onRun: (mode: string) => Promise<void>;
    workflowId: number;
    saveWorkflow: (updateWorkflowDefinition?: boolean) => Promise<void>;
    user: { id: string; email?: string };
    getAccessToken: () => Promise<string>;
    onPhoneCallClick: () => void;
}

export const WorkflowEditorHeader = ({
    workflowName,
    isDirty,
    workflowValidationErrors,
    rfInstance,
    saveWorkflow,
    onRun,
    onPhoneCallClick,
    workflowId,
}: WorkflowEditorHeaderProps) => {
    const router = useRouter();
    const [savingWorkflow, setSavingWorkflow] = useState(false);

    const hasValidationErrors = workflowValidationErrors.length > 0;
    const isCallDisabled = isDirty || hasValidationErrors;

    const handleSave = async () => {
        setSavingWorkflow(true);
        await saveWorkflow();
        setSavingWorkflow(false);
    };

    const handleBack = () => {
        router.push("/workflow");
    };

    const handleDownloadWorkflow = () => {
        if (!rfInstance.current) return;

        const workflowDefinition = rfInstance.current.toObject();
        const exportData = {
            name: workflowName,
            workflow_definition: workflowDefinition,
        };

        const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `${workflowName}.json`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
    };

    return (
        <div className="flex items-center justify-between w-full h-14 px-4 bg-[#1a1a1a] border-b border-[#2a2a2a]">
            {/* Left section: Back button + Workflow name */}
            <div className="flex items-center gap-3">
                <button
                    onClick={handleBack}
                    className="flex items-center justify-center w-8 h-8 rounded-lg hover:bg-[#2a2a2a] transition-colors"
                >
                    <ArrowLeft className="w-5 h-5 text-gray-400" />
                </button>

                <div className="flex items-center gap-2">
                    <h1 className="text-base font-medium text-white">
                        {workflowName}
                    </h1>
                </div>
            </div>

            {/* Right section: Unsaved indicator + Call button + Save button */}
            <div className="flex items-center gap-3">
                {/* Unsaved changes indicator */}
                {isDirty && (
                    <div className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-yellow-500/30 bg-yellow-500/10">
                        <div className="w-2 h-2 rounded-full bg-yellow-500" />
                        <span className="text-sm text-yellow-500">Unsaved changes</span>
                    </div>
                )}

                {/* Validation errors indicator */}
                {hasValidationErrors && (
                    <Popover>
                        <PopoverTrigger asChild>
                            <button className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-red-500/30 bg-red-500/10 hover:bg-red-500/20 transition-colors cursor-pointer">
                                <div className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                                <AlertCircle className="w-4 h-4 text-red-500" />
                                <span className="text-sm text-red-500">
                                    {workflowValidationErrors.length} {workflowValidationErrors.length === 1 ? "error" : "errors"}
                                </span>
                            </button>
                        </PopoverTrigger>
                        <PopoverContent
                            align="end"
                            className="w-80 bg-[#1a1a1a] border-[#3a3a3a] p-0"
                        >
                            <div className="px-4 py-3 border-b border-[#3a3a3a]">
                                <h3 className="text-sm font-medium text-white">Validation Errors</h3>
                            </div>
                            <div className="max-h-64 overflow-y-auto">
                                {workflowValidationErrors.map((error, index) => (
                                    <div
                                        key={index}
                                        className="px-4 py-3 border-b border-[#2a2a2a] last:border-b-0"
                                    >
                                        <div className="flex items-start gap-2">
                                            <AlertCircle className="w-4 h-4 text-red-500 mt-0.5 flex-shrink-0" />
                                            <div className="flex-1 min-w-0">
                                                {(error.kind === "node" || error.kind === "edge") && error.id && (
                                                    <p className="text-xs text-gray-400 mb-1">
                                                        {error.kind === "node" ? "Node" : "Edge"}: {error.id}
                                                        {error.field && <span className="text-gray-500"> • {error.field}</span>}
                                                    </p>
                                                )}
                                                <p className="text-sm text-white break-words">
                                                    {error.message}
                                                </p>
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </PopoverContent>
                    </Popover>
                )}

                {/* Call button with dropdown */}
                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <Button
                            variant="outline"
                            className="flex items-center gap-2 bg-transparent border-[#3a3a3a] hover:bg-[#2a2a2a] text-white"
                            disabled={isCallDisabled}
                        >
                            <Phone className="w-4 h-4" />
                            Call
                            <ChevronDown className="w-4 h-4" />
                        </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="bg-[#1a1a1a] border-[#3a3a3a]">
                        <DropdownMenuItem
                            onClick={() => onRun(WORKFLOW_RUN_MODES.SMALL_WEBRTC)}
                            className="text-white hover:bg-[#2a2a2a] cursor-pointer"
                        >
                            <Phone className="w-4 h-4 mr-2" />
                            Web Call
                        </DropdownMenuItem>
                        <DropdownMenuItem
                            onClick={() => {
                                // Delay opening dialog to next event cycle to allow DropdownMenu
                                // to clean up first, preventing pointer-events: none stuck on body
                                // See: https://github.com/radix-ui/primitives/issues/1241
                                setTimeout(onPhoneCallClick, 0);
                            }}
                            className="text-white hover:bg-[#2a2a2a] cursor-pointer"
                        >
                            <Phone className="w-4 h-4 mr-2" />
                            Phone Call
                        </DropdownMenuItem>
                    </DropdownMenuContent>
                </DropdownMenu>

                {/* Save button */}
                <Button
                    onClick={handleSave}
                    disabled={!isDirty || savingWorkflow}
                    className="bg-teal-600 hover:bg-teal-700 text-white px-4"
                >
                    {savingWorkflow ? (
                        <>
                            <LoaderCircle className="w-4 h-4 mr-2 animate-spin" />
                            Saving...
                        </>
                    ) : (
                        "Save"
                    )}
                </Button>

                {/* More options dropdown */}
                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <Button
                            variant="ghost"
                            size="icon"
                            className="text-gray-400 hover:text-white hover:bg-[#2a2a2a]"
                        >
                            <MoreVertical className="w-5 h-5" />
                        </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="bg-[#1a1a1a] border-[#3a3a3a]">
                        <DropdownMenuItem
                            onClick={() => router.push(`/workflow/${workflowId}/runs`)}
                            className="text-white hover:bg-[#2a2a2a] cursor-pointer"
                        >
                            <History className="w-4 h-4 mr-2" />
                            View Runs
                        </DropdownMenuItem>
                        <DropdownMenuItem
                            onClick={handleDownloadWorkflow}
                            className="text-white hover:bg-[#2a2a2a] cursor-pointer"
                        >
                            <Download className="w-4 h-4 mr-2" />
                            Download Workflow
                        </DropdownMenuItem>
                    </DropdownMenuContent>
                </DropdownMenu>
            </div>
        </div>
    );
};
