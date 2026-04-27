"use client";

import { ReactFlowInstance } from "@xyflow/react";
import { AlertCircle, ArrowLeft, ChevronDown, Copy, Download, Eye, History, LoaderCircle, Menu, MoreVertical, Phone, Rocket, Pencil } from "lucide-react";
import { useRouter } from "next/navigation";
import posthog from "posthog-js";
import { useState, useRef, useEffect } from "react";
import { toast } from "sonner";

import { useWorkflowStore } from "@/app/workflow/[workflowId]/stores/workflowStore";
import {
    duplicateWorkflowEndpointApiV1WorkflowWorkflowIdDuplicatePost,
    publishWorkflowApiV1WorkflowWorkflowIdPublishPost,
} from "@/client/sdk.gen";
import { WorkflowError } from "@/client/types.gen";
import { FlowEdge, FlowNode } from "@/components/flow/types";
import { GitHubStarBadge } from "@/components/layout/GitHubStarBadge";
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
import { useSidebar } from "@/components/ui/sidebar";
import { PostHogEvent } from "@/constants/posthog-events";
import { WORKFLOW_RUN_MODES } from "@/constants/workflowRunModes";

interface WorkflowEditorHeaderProps {
    workflowName: string;
    isDirty: boolean;
    workflowValidationErrors: WorkflowError[];
    rfInstance: React.RefObject<ReactFlowInstance<FlowNode, FlowEdge> | null>;
    onRun: (mode: string) => Promise<void>;
    workflowId: number;
    saveWorkflow: (updateWorkflowDefinition?: boolean) => Promise<void>;
    saveWorkflowConfigurations: (updates: { name: string }) => Promise<void>;
    user: { id: string; email?: string };
    onPhoneCallClick: () => void;
    onHistoryClick: () => void;
    activeVersionLabel?: string;
    isViewingHistoricalVersion: boolean;
    onBackToDraft: () => void;
    hasDraft: boolean;
    onPublished: () => void;
}

export const WorkflowEditorHeader = ({
    workflowName,
    isDirty,
    workflowValidationErrors,
    rfInstance,
    saveWorkflow,
    saveWorkflowConfigurations,
    onRun,
    onPhoneCallClick,
    onHistoryClick,
    activeVersionLabel,
    isViewingHistoricalVersion,
    onBackToDraft,
    hasDraft,
    onPublished,
    workflowId,
}: WorkflowEditorHeaderProps) => {
    const router = useRouter();
    const { toggleSidebar } = useSidebar();
    const [savingWorkflow, setSavingWorkflow] = useState(false);
    const [duplicating, setDuplicating] = useState(false);
    const [publishing, setPublishing] = useState(false);

    const setWorkflowNameGlobal = useWorkflowStore((state) => state.setWorkflowName);
    const [isEditingName, setIsEditingName] = useState(false);
    const [editedName, setEditedName] = useState(workflowName);
    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        setEditedName(workflowName);
    }, [workflowName]);

    useEffect(() => {
        if (isEditingName && inputRef.current) {
            inputRef.current.focus();
        }
    }, [isEditingName]);

    const handleRenameSubmit = async () => {
        const trimmed = editedName.trim();
        if (!trimmed || trimmed === workflowName) {
            setIsEditingName(false);
            setEditedName(workflowName);
            return;
        }
        setIsEditingName(false);
        const promise = saveWorkflowConfigurations({ name: trimmed });
        toast.promise(promise, {
            loading: "Renaming...",
            success: "Renamed successfully",
            error: "Failed to rename",
        });
        try {
            await promise;
            setWorkflowNameGlobal(trimmed);
        } catch {
            setEditedName(workflowName);
        }
    };

    const hasValidationErrors = workflowValidationErrors.length > 0;
    const isCallDisabled = isDirty || hasValidationErrors;

    const handleSave = async () => {
        setSavingWorkflow(true);
        await saveWorkflow();
        setSavingWorkflow(false);
    };

    const handlePublish = async () => {
        if (publishing) return;
        setPublishing(true);
        const promise = publishWorkflowApiV1WorkflowWorkflowIdPublishPost({
            path: { workflow_id: workflowId },
        });
        toast.promise(promise, {
            loading: "Publishing...",
            success: "Workflow published successfully",
            error: "Failed to publish workflow",
        });
        try {
            await promise;
            onPublished();
        } finally {
            setPublishing(false);
        }
    };

    const handleBack = () => {
        router.push("/workflow");
    };

    const handleDuplicate = async () => {
        if (duplicating) return;
        setDuplicating(true);
        const promise = duplicateWorkflowEndpointApiV1WorkflowWorkflowIdDuplicatePost({
            path: { workflow_id: workflowId },
        });
        toast.promise(promise, {
            loading: "Duplicating workflow...",
            success: "Workflow duplicated successfully",
            error: "Failed to duplicate workflow",
        });
        try {
            const { data } = await promise;
            if (data?.id) {
                router.push(`/workflow/${data.id}`);
            }
        } finally {
            setDuplicating(false);
        }
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
            {/* Left section: Mobile menu + Back button + Workflow name */}
            <div className="flex items-center gap-3 mr-4">
                <button
                    onClick={toggleSidebar}
                    className="flex items-center justify-center w-8 h-8 rounded-lg hover:bg-[#2a2a2a] transition-colors md:hidden"
                    aria-label="Open menu"
                >
                    <Menu className="w-5 h-5 text-gray-400" />
                </button>
                <button
                    onClick={handleBack}
                    className="flex items-center justify-center w-8 h-8 rounded-lg hover:bg-[#2a2a2a] transition-colors"
                >
                    <ArrowLeft className="w-5 h-5 text-gray-400" />
                </button>

                <div className="flex items-center gap-2">
                    {isEditingName && !isViewingHistoricalVersion ? (
                        <input
                            ref={inputRef}
                            value={editedName}
                            onChange={(e) => setEditedName(e.target.value)}
                            onBlur={handleRenameSubmit}
                            onKeyDown={(e) => {
                                if (e.key === "Enter") handleRenameSubmit();
                                else if (e.key === "Escape") {
                                    setIsEditingName(false);
                                    setEditedName(workflowName);
                                }
                            }}
                            className="text-base font-medium text-white bg-[#1a1a1a] border border-[#3a3a3a] rounded px-1.5 py-0.5 focus:outline-none focus:border-gray-400 w-[150px] md:w-[300px]"
                        />
                    ) : (
                        <div 
                            className={`flex items-center gap-2 ${!isViewingHistoricalVersion ? "cursor-pointer group" : ""}`}
                            onClick={() => !isViewingHistoricalVersion && setIsEditingName(true)}
                        >
                            <h1 className="text-base font-medium text-white whitespace-nowrap">
                                <span className="md:hidden">
                                    {workflowName.length > 8 ? `${workflowName.slice(0, 8)}…` : workflowName}
                                </span>
                                <span className="hidden md:inline">{workflowName}</span>
                            </h1>
                            {!isViewingHistoricalVersion && (
                                <Pencil className="w-4 h-4 text-gray-500 opacity-0 group-hover:opacity-100 transition-opacity" />
                            )}
                        </div>
                    )}
                </div>
            </div>

            {/* Right section: Version + Unsaved indicator + Call button + Save button */}
            <div className="flex items-center gap-3">
                {/* Read-only banner when viewing a historical version */}
                {isViewingHistoricalVersion && (
                    <div className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-blue-500/30 bg-blue-500/10">
                        <Eye className="w-4 h-4 text-blue-400" />
                        <span className="text-sm text-blue-400">
                            Viewing {activeVersionLabel} — Read only
                        </span>
                    </div>
                )}

                {/* Back to Draft button when viewing history */}
                {isViewingHistoricalVersion && (
                    <Button
                        onClick={onBackToDraft}
                        className="bg-teal-600 hover:bg-teal-700 text-white px-4"
                    >
                        Back to Draft
                    </Button>
                )}

                {/* Version history button */}
                <button
                    onClick={onHistoryClick}
                    className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-[#3a3a3a] hover:bg-[#2a2a2a] transition-colors cursor-pointer"
                >
                    <History className="w-4 h-4 text-gray-400" />
                    {activeVersionLabel && !isViewingHistoricalVersion && (
                        <span className="text-sm text-gray-300">{activeVersionLabel}</span>
                    )}
                </button>

                {/* Unsaved changes indicator (hidden when viewing history) */}
                {isDirty && !isViewingHistoricalVersion && (
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
                            </button