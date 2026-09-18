"use client";

import { ReactFlowInstance } from "@xyflow/react";
import { AlertCircle, ArrowLeft, Bot, Clipboard, Copy, Download, Eye, History, LoaderCircle, Menu, MoreVertical, Pencil, Phone, Rocket } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { toast } from "sonner";

import {
    duplicateWorkflowEndpointApiV1WorkflowWorkflowIdDuplicatePost,
    publishWorkflowApiV1WorkflowWorkflowIdPublishPost,
} from "@/client/sdk.gen";
import { WorkflowError } from "@/client/types.gen";
import { FlowEdge, FlowNode } from "@/components/flow/types";
import { Button } from "@/components/ui/button";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from "@/components/ui/popover";
import { useSidebar } from "@/components/ui/sidebar";
import { copyTextToClipboard } from "@/lib/clipboard";

interface WorkflowEditorHeaderProps {
    workflowName: string;
    isDirty: boolean;
    workflowValidationErrors: WorkflowError[];
    rfInstance: React.RefObject<ReactFlowInstance<FlowNode, FlowEdge> | null>;
    workflowId: number;
    workflowUuid?: string;
    saveWorkflow: (updateWorkflowDefinition?: boolean) => Promise<void>;
    user: { id: string; email?: string };
    onPhoneCallClick: () => void;
    onTestAgentClick: () => void;
    onHistoryClick: () => void;
    activeVersionLabel?: string;
    isViewingHistoricalVersion: boolean;
    onBackToDraft: () => void;
    hasDraft: boolean;
    onPublished: () => void;
    renameWorkflow: (newName: string) => Promise<void>;
}

export const WorkflowEditorHeader = ({
    workflowName,
    isDirty,
    workflowValidationErrors,
    rfInstance,
    saveWorkflow,
    onPhoneCallClick,
    onTestAgentClick,
    onHistoryClick,
    activeVersionLabel,
    isViewingHistoricalVersion,
    onBackToDraft,
    hasDraft,
    onPublished,
    workflowId,
    workflowUuid,
    renameWorkflow,
}: WorkflowEditorHeaderProps) => {
    const router = useRouter();
    const { toggleSidebar } = useSidebar();
    const [savingWorkflow, setSavingWorkflow] = useState(false);
    const [duplicating, setDuplicating] = useState(false);
    const [publishing, setPublishing] = useState(false);
    // One discriminated-union state instead of (isEditingName, nameDraft,
    // nameError, isRenaming): they're not independent — error and saving are
    // mutually exclusive, and both are meaningless in the display state. The
    // union makes the bad combinations unrepresentable and structurally
    // prevents the Enter→disable-input→blur→re-fire race.
    type RenameState =
        | { kind: "display" }
        | { kind: "editing"; draft: string; error: string | null }
        | { kind: "saving"; draft: string };
    const [rename, setRename] = useState<RenameState>({ kind: "display" });
    const nameInputRef = useRef<HTMLInputElement>(null);
    const renameButtonRef = useRef<HTMLButtonElement>(null);

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

    const handleCopyAgentUuid = async () => {
        if (!workflowUuid) {
            toast.error("Agent UUID not available");
            return;
        }
        try {
            await copyTextToClipboard(workflowUuid);
            toast.success("Agent UUID copied");
        } catch {
            toast.error("Failed to copy Agent UUID");
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

    const enterEditMode = () => {
        setRename({ kind: "editing", draft: workflowName, error: null });
    };

    const exitEditMode = () => {
        setRename({ kind: "display" });
        // Return focus to the pencil button so keyboard users aren't stranded.
        // Defer to next tick so React commits the input unmount first.
        setTimeout(() => renameButtonRef.current?.focus(), 0);
    };

    const attemptSave = async () => {
        // Only "editing" can initiate a save. This also guards against the
        // blur fired when disabling the input transitions us to "saving".
        if (rename.kind !== "editing") return;
        const trimmed = rename.draft.trim();
        if (trimmed.length === 0) {
            setRename({ ...rename, error: "Name cannot be empty" });
            return;
        }
        if (trimmed === workflowName) {
            // No-op: exit cleanly with no API call.
            exitEditMode();
            return;
        }
        setRename({ kind: "saving", draft: rename.draft });
        try {
            await renameWorkflow(trimmed);
            // Success: store update already propagated workflowName. Exit edit mode.
            exitEditMode();
        } catch {
            // Roll back: keep user's typed value, reopen the input, focus it,
            // surface a sonner toast (matches existing duplicate/publish failure pattern).
            toast.error("Failed to rename workflow");
            setRename({ kind: "editing", draft: trimmed, error: null });
            setTimeout(() => nameInputRef.current?.focus(), 0);
        }
    };

    const handleRenameKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
        if (event.key === "Enter") {
            event.preventDefault();
            void attemptSave();
        } else if (event.key === "Escape") {
            event.preventDefault();
            exitEditMode();
        }
    };

    const handleRenameBlur = () => {
        // Ignore the blur fired when the input is disabled during save.
        if (rename.kind !== "editing") return;
        // On blur with empty/whitespace, revert silently to display mode so the user is never trapped.
        if (rename.draft.trim().length === 0) {
            exitEditMode();
            return;
        }
        void attemptSave();
    };

    return (
        <div className="flex items-center justify-between w-full h-12 px-4 bg-background border-b border-border">
            {/* Left section: Mobile menu + Back button + Workflow name */}
            <div className="flex items-center gap-3 mr-4">
                <button
                    onClick={toggleSidebar}
                    className="flex items-center justify-center w-8 h-8 rounded-lg hover:bg-accent transition-colors md:hidden"
                    aria-label="Open menu"
                >
                    <Menu className="w-4 h-4 text-muted-foreground" />
                </button>
                <button
                    onClick={handleBack}
                    className="flex items-center justify-center w-8 h-8 rounded-lg hover:bg-accent transition-colors"
                >
                    <ArrowLeft className="w-4 h-4 text-muted-foreground" />
                </button>

                <div className="flex items-center gap-2">
                    {rename.kind !== "display" ? (
                        <div className="flex flex-col gap-1">
                            <Input
                                ref={nameInputRef}
                                value={rename.draft}
                                onChange={(e) => {
                                    if (rename.kind === "editing") {
                                        setRename({ ...rename, draft: e.target.value, error: null });
                                    }
                                }}
                                onKeyDown={handleRenameKeyDown}
                                onBlur={handleRenameBlur}
                                disabled={rename.kind === "saving"}
                                autoFocus
                                onFocus={(e) => e.currentTarget.select()}
                                aria-label="Workflow name"
                                aria-invalid={rename.kind === "editing" && rename.error !== null}
                                className="h-8 max-w-xs text-sm font-medium"
                            />
                            {rename.kind === "editing" && rename.error && (
                                <span className="text-xs text-destructive" role="alert">{rename.error}</span>
                            )}
                        </div>
                    ) : (
                        <>
                            <h1 className="text-sm font-semibold text-foreground whitespace-nowrap truncate max-w-[14rem] md:max-w-md">
                                <span className="md:hidden">
                                    {workflowName.length > 8 ? `${workflowName.slice(0, 8)}…` : workflowName}
                                </span>
                                <span className="hidden md:inline">{workflowName}</span>
                            </h1>
                            {!isViewingHistoricalVersion && (
                                <button
                                    ref={renameButtonRef}
                                    type="button"
                                    onClick={enterEditMode}
                                    aria-label="Rename workflow"
                                    className="flex items-center justify-center w-7 h-7 rounded-md hover:bg-accent transition-colors"
                                >
                                    <Pencil className="w-3.5 h-3.5 text-muted-foreground" />
                                </button>
                            )}
                        </>
                    )}
                </div>
            </div>

            {/* Right section: Version + status + tester/call actions + save */}
            <div className="flex items-center gap-2">
                {/* Read-only banner when viewing a historical version */}
                {isViewingHistoricalVersion && (
                    <div className="flex items-center gap-2 px-2.5 py-1 rounded-md border border-blue-500/30 bg-blue-500/10">
                        <Eye className="w-3.5 h-3.5 text-blue-500 dark:text-blue-400" />
                        <span className="text-xs text-blue-600 dark:text-blue-400">
                            Viewing {activeVersionLabel} - Read only
                        </span>
                    </div>
                )}

                {/* Back to Draft button when viewing history */}
                {isViewingHistoricalVersion && (
                    <Button
                        size="sm"
                        onClick={onBackToDraft}
                        className="bg-cta hover:bg-cta/90 text-cta-foreground"
                    >
                        Back to Draft
                    </Button>
                )}

                {/* Version history button */}
                <Button
                    variant="outline"
                    size="sm"
                    onClick={onHistoryClick}
                    className="gap-1.5"
                >
                    <History className="w-3.5 h-3.5" />
                    {activeVersionLabel && !isViewingHistoricalVersion && (
                        <span className="text-xs">{activeVersionLabel}</span>
                    )}
                </Button>

                {/* Unsaved changes indicator (hidden when viewing history) */}
                {isDirty && !isViewingHistoricalVersion && (
                    <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-yellow-500/30 bg-yellow-500/10">
                        <div className="w-1.5 h-1.5 rounded-full bg-yellow-500" />
                        <span className="text-xs text-yellow-600 dark:text-yellow-500">Unsaved</span>
                    </div>
                )}

                {/* Validation errors indicator */}
                {hasValidationErrors && (
                    <Popover>
                        <PopoverTrigger asChild>
                            <button className="flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-red-500/30 bg-red-500/10 hover:bg-red-500/20 transition-colors cursor-pointer">
                                <div className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
                                <AlertCircle className="w-3.5 h-3.5 text-red-500" />
                                <span className="text-xs text-red-500">
                                    {workflowValidationErrors.length} {workflowValidationErrors.length === 1 ? "error" : "errors"}
                                </span>
                            </button>
                        </PopoverTrigger>
                        <PopoverContent
                            align="end"
                            className="w-80 p-0"
                        >
                            <div className="px-4 py-3 border-b border-border">
                                <h3 className="text-sm font-medium text-foreground">Validation Errors</h3>
                            </div>
                            <div className="max-h-64 overflow-y-auto">
                                {workflowValidationErrors.map((error, index) => (
                                    <div
                                        key={index}
                                        className="px-4 py-3 border-b border-border last:border-b-0"
                                    >
                                        <div className="flex items-start gap-2">
                                            <AlertCircle className="w-3.5 h-3.5 text-red-500 mt-0.5 flex-shrink-0" />
                                            <div className="flex-1 min-w-0">
                                                {(error.kind === "node" || error.kind === "edge") && error.id && (
                                                    <p className="text-xs text-muted-foreground mb-1">
                                                        {error.kind === "node" ? "Node" : "Edge"}: {error.id}
                                                        {error.field && <span className="text-muted-foreground/60"> • {error.field}</span>}
                                                    </p>
                                                )}
                                                <p className="text-sm text-foreground break-words">
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

                {/* Publish button (only when on draft with no unsaved changes) */}
                {!isViewingHistoricalVersion && hasDraft && (
                    <Button
                        onClick={handlePublish}
                        disabled={isDirty || publishing || hasValidationErrors}
                        variant="outline"
                        size="sm"
                    >
                        {publishing ? (
                            <>
                                <LoaderCircle className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                                Publishing...
                            </>
                        ) : (
                            <>
                                <Rocket className="w-3.5 h-3.5 mr-1.5" />
                                Publish
                            </>
                        )}
                    </Button>
                )}

                {!isViewingHistoricalVersion && (
                    <Button
                        variant="outline"
                        size="sm"
                        className="gap-1.5"
                        disabled={isCallDisabled}
                        onClick={onPhoneCallClick}
                    >
                        <Phone className="w-3.5 h-3.5" />
                        Phone Call
                    </Button>
                )}

                <Button
                    variant="outline"
                    size="sm"
                    className="gap-1.5"
                    onClick={onTestAgentClick}
                >
                    <Bot className="w-3.5 h-3.5" />
                    Test Agent
                </Button>

                {/* Save button (only shown when editing the draft) */}
                {!isViewingHistoricalVersion && (
                    <Button
                        size="sm"
                        onClick={handleSave}
                        disabled={!isDirty || savingWorkflow}
                        className="bg-cta hover:bg-cta/90 text-cta-foreground"
                    >
                        {savingWorkflow ? (
                            <>
                                <LoaderCircle className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                                Saving...
                            </>
                        ) : (
                            "Save"
                        )}
                    </Button>
                )}

                {/* More options dropdown */}
                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8 text-muted-foreground hover:text-foreground"
                        >
                            <MoreVertical className="w-4 h-4" />
                        </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                        <DropdownMenuItem
                            onClick={() => router.push(`/workflow/${workflowId}/runs`)}
                            className="cursor-pointer"
                        >
                            <History className="w-4 h-4 mr-2" />
                            View Runs
                        </DropdownMenuItem>
                        <DropdownMenuItem
                            onClick={handleDuplicate}
                            disabled={duplicating}
                            className="cursor-pointer"
                        >
                            {duplicating ? (
                                <LoaderCircle className="w-4 h-4 mr-2 animate-spin" />
                            ) : (
                                <Copy className="w-4 h-4 mr-2" />
                            )}
                            {duplicating ? "Duplicating..." : "Duplicate Workflow"}
                        </DropdownMenuItem>
                        <DropdownMenuItem
                            onClick={handleDownloadWorkflow}
                            className="cursor-pointer"
                        >
                            <Download className="w-4 h-4 mr-2" />
                            Download Workflow
                        </DropdownMenuItem>
                        <DropdownMenuItem
                            onClick={handleCopyAgentUuid}
                            disabled={!workflowUuid}
                            className="cursor-pointer"
                        >
                            <Clipboard className="w-4 h-4 mr-2" />
                            Copy Agent UUID
                        </DropdownMenuItem>
                    </DropdownMenuContent>
                </DropdownMenu>

            </div>
        </div>
    );
};
