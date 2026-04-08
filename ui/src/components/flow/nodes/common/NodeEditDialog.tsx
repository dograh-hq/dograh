import { AlertCircle, ExternalLink } from "lucide-react";
import { ReactNode, useCallback, useEffect, useState } from "react";

import { useWorkflowOptional } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import { FlowNodeData } from "@/components/flow/types";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

interface NodeEditDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    nodeData: FlowNodeData;
    title: string;
    children: ReactNode;
    onSave?: () => void;
    error?: string | null;
    isDirty?: boolean;
    documentationUrl?: string;
}

export const NodeEditDialog = ({
    open,
    onOpenChange,
    nodeData,
    title,
    children,
    onSave,
    error,
    isDirty = false,
    documentationUrl,
}: NodeEditDialogProps) => {
    const readOnly = useWorkflowOptional()?.readOnly ?? false;
    const [showDiscardAlert, setShowDiscardAlert] = useState(false);

    const handleClose = () => onOpenChange(false);

    const handleSave = useCallback(() => {
        if (onSave) {
            onSave();
        }
    }, [onSave]);

    // Intercept dialog close attempts when dirty
    const handleOpenChange = useCallback((newOpen: boolean) => {
        // If trying to close and form is dirty, show confirmation
        if (!newOpen && isDirty) {
            setShowDiscardAlert(true);
            return;
        }
        onOpenChange(newOpen);
    }, [isDirty, onOpenChange]);

    // Handle confirmed discard
    const handleConfirmDiscard = useCallback(() => {
        setShowDiscardAlert(false);
        onOpenChange(false);
    }, [onOpenChange]);

    // Handle Cmd+S / Ctrl+S keyboard shortcut to save
    useEffect(() => {
        if (!open || readOnly) return;

        const handleKeyDown = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 's') {
                e.preventDefault();
                e.stopImmediatePropagation();
                handleSave();
            }
        };

        window.addEventListener('keydown', handleKeyDown, true);
        return () => window.removeEventListener('keydown', handleKeyDown, true);
    }, [open, readOnly, handleSave]);

    return (
        <Dialog open={open} onOpenChange={handleOpenChange}>
            <DialogContent
                className="max-h-[85vh] overflow-y-auto"
                style={{ maxWidth: "1200px", width: "95vw" }}
            >
                <DialogHeader>
                    <div className="flex items-center justify-between">
                        <DialogTitle>{title}</DialogTitle>
                        {documentationUrl && (
                            <a
                                href={documentationUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors pr-6"
                            >
                                Docs
                                <ExternalLink className="h-3.5 w-3.5" />
                            </a>
                        )}
                    </div>
                    <DialogDescription>
                        Configure the settings for this node in your workflow.
                    </DialogDescription>
                    {nodeData.invalid && nodeData.validationMessage && (
                        <div className="mt-2 flex items-center gap-2 rounded-md bg-red-50 p-2 text-sm text-red-500 border border-red-200">
                            <AlertCircle className="h-4 w-4" />
                            <span>{nodeData.validationMessage}</span>
                        </div>
                    )}
                </DialogHeader>
                <div className="grid gap-4 py-4">
                    {children}
                </div>
                {error && (
                    <div className="flex items-center gap-2 rounded-md bg-red-50 p-3 text-sm text-red-600 border border-red-200">
                        <AlertCircle className="h-4 w-4 flex-shrink-0" />
                        <span>{error}</span>
                    </div>
                )}
                <DialogFooter>
                    <div className="flex items-center gap-2">
                        <Button
                            variant="outline"
                            onClick={isDirty ? () => setShowDiscardAlert(true) : handleClose}
                        >
                            Cancel
                        </Button>
                        <Button onClick={handleSave} disabled={readOnly}>
                            {readOnly ? "Read Only" : "Save"}
                        </Button>
                    </div>
                </DialogFooter>
            </DialogContent>

            {/* Discard changes confirmation dialog */}
            <AlertDialog open={showDiscardAlert} onOpenChange={setShowDiscardAlert}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Discard changes?</AlertDialogTitle>
                        <AlertDialogDescription>
                            You have unsaved changes. Are you sure you want to discard them?
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Keep Editing</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={handleConfirmDiscard}
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        >
                            Discard
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </Dialog>
    );
};
