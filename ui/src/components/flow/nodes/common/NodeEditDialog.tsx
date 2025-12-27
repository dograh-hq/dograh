import { AlertCircle } from "lucide-react";
import { ReactNode } from "react";

import { FlowNodeData } from "@/components/flow/types";
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
}

export const NodeEditDialog = ({
    open,
    onOpenChange,
    nodeData,
    title,
    children,
    onSave,
    error
}: NodeEditDialogProps) => {
    const handleClose = () => onOpenChange(false);

    const handleSave = () => {
        if (onSave) {
            onSave();
        }
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent
                className="max-h-[85vh] overflow-y-auto"
                style={{ maxWidth: "1200px", width: "95vw" }}
            >
                <DialogHeader>
                    <DialogTitle>{title}</DialogTitle>
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
                        <Button variant="outline" onClick={handleClose}>Cancel</Button>
                        <Button onClick={handleSave}>Save</Button>
                    </div>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};
