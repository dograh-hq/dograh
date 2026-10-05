"use client";

import { LoaderCircle, MoreVertical } from "lucide-react";
import { useId, useRef, useState } from "react";
import { toast } from "sonner";

import { updateWorkflowVersionMetadataApiV1WorkflowWorkflowIdVersionsDefinitionIdMetadataPatch } from "@/client/sdk.gen";
import type { WorkflowVersionMetadataResponse, WorkflowVersionResponse } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { detailFromError } from "@/lib/apiError";

import { VersionMetadataFields } from "./VersionMetadataFields";

interface EditVersionMetadataPopoverProps {
    workflowId: number;
    version: WorkflowVersionResponse;
    onUpdated: (metadata: WorkflowVersionMetadataResponse) => void;
}

export function EditVersionMetadataPopover({ workflowId, version, onUpdated }: EditVersionMetadataPopoverProps) {
    const titleId = useId();
    const [open, setOpen] = useState(false);
    const [versionName, setVersionName] = useState("");
    const [description, setDescription] = useState("");
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const submitting = useRef(false);

    const handleOpenChange = (nextOpen: boolean) => {
        if (submitting.current) return;
        if (nextOpen) {
            setVersionName(version.version_name ?? "");
            setDescription(version.change_description ?? "");
        }
        setError(null);
        setOpen(nextOpen);
    };

    const handleSave = async (event: React.FormEvent) => {
        event.preventDefault();
        if (submitting.current) return;
        submitting.current = true;
        setSaving(true);
        setError(null);
        try {
            const response = await updateWorkflowVersionMetadataApiV1WorkflowWorkflowIdVersionsDefinitionIdMetadataPatch({
                path: { workflow_id: workflowId, definition_id: version.id },
                body: { version_name: versionName.trim() || null, change_description: description.trim() || null },
            });
            if (response.error || !response.data) {
                setError(detailFromError(response.error, "Failed to update version details"));
                return;
            }
            onUpdated(response.data);
            setOpen(false);
            toast.success("Version details updated");
        } catch {
            setError("Could not update version details. Please try again.");
        } finally {
            submitting.current = false;
            setSaving(false);
        }
    };

    return (
        <Popover open={open} onOpenChange={handleOpenChange}>
            <PopoverTrigger asChild>
                <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    disabled={saving}
                    aria-label={`Edit details for v${version.version_number}`}
                    className="h-7 w-7 text-gray-400 hover:bg-[#303030] hover:text-white"
                >
                    <MoreVertical className="h-4 w-4" />
                </Button>
            </PopoverTrigger>
            <PopoverContent
                side="left"
                align="start"
                sideOffset={8}
                aria-labelledby={titleId}
                className="z-60 w-80 max-w-[calc(100vw-2rem)] space-y-3 border-[#3a3a3a] bg-[#1a1a1a] text-white"
            >
                <h3 id={titleId} className="text-sm font-medium">Edit v{version.version_number} details</h3>
                <form onSubmit={handleSave} className="space-y-3">
                    <VersionMetadataFields
                        versionName={versionName}
                        description={description}
                        onVersionNameChange={setVersionName}
                        onDescriptionChange={setDescription}
                        disabled={saving}
                    />
                    {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
                    <div className="flex justify-end gap-2">
                        <Button type="button" size="sm" variant="ghost" disabled={saving} onClick={() => handleOpenChange(false)}>Cancel</Button>
                        <Button type="submit" size="sm" disabled={saving} className="bg-teal-600 text-white hover:bg-teal-700">
                            {saving && <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />}
                            {saving ? "Saving…" : "Save changes"}
                        </Button>
                    </div>
                </form>
            </PopoverContent>
        </Popover>
    );
}
