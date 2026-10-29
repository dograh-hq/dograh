"use client";

import { LoaderCircle, Rocket } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { publishWorkflowApiV1WorkflowWorkflowIdPublishPost } from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { detailFromError } from "@/lib/apiError";

import { VersionMetadataFields } from "./VersionMetadataFields";

interface PublishWorkflowPopoverProps {
    workflowId: number;
    canPublish: boolean;
    onPublished: () => void;
}

export function PublishWorkflowPopover({ workflowId, canPublish, onPublished }: PublishWorkflowPopoverProps) {
    const [open, setOpen] = useState(false);
    const [versionName, setVersionName] = useState("");
    const [description, setDescription] = useState("");
    const [publishing, setPublishing] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const submitting = useRef(false);

    const handlePublish = async (event: React.FormEvent) => {
        event.preventDefault();
        if (submitting.current || !canPublish) return;
        submitting.current = true;
        setPublishing(true);
        setError(null);
        try {
            const response = await publishWorkflowApiV1WorkflowWorkflowIdPublishPost({
                path: { workflow_id: workflowId },
                body: { version_name: versionName.trim() || null, change_description: description.trim() || null },
            });
            if (response.error) {
                setError(detailFromError(response.error, "Failed to publish workflow"));
                return;
            }
            toast.success("Workflow published successfully");
            setVersionName("");
            setDescription("");
            setOpen(false);
            onPublished();
        } catch {
            setError("Could not publish the workflow. Please try again.");
        } finally {
            submitting.current = false;
            setPublishing(false);
        }
    };

    return (
        <Popover open={open} onOpenChange={(nextOpen) => {
            if (!submitting.current) {
                setError(null);
                setOpen(nextOpen);
            }
        }}>
            <PopoverTrigger asChild>
                <Button
                    type="button"
                    disabled={!canPublish || publishing}
                    variant="outline"
                    className="border-[#3a3a3a] bg-transparent hover:bg-[#2a2a2a] text-white px-4"
                >
                    <Rocket className="w-4 h-4 mr-2" />
                    Publish
                </Button>
            </PopoverTrigger>
            <PopoverContent
                side="bottom"
                align="end"
                sideOffset={8}
                aria-labelledby="publish-version-title"
                aria-describedby="publish-version-hint"
                className="w-80 max-w-[calc(100vw-2rem)] space-y-3 border-[#3a3a3a] bg-[#1a1a1a] text-white"
            >
                <div className="space-y-1">
                    <h3 id="publish-version-title" className="text-sm font-medium">Publish version</h3>
                    <p id="publish-version-hint" className="text-xs text-gray-400">Add optional notes for Version History.</p>
                </div>
                <form onSubmit={handlePublish} className="space-y-3">
                    <VersionMetadataFields
                        versionName={versionName}
                        description={description}
                        onVersionNameChange={setVersionName}
                        onDescriptionChange={setDescription}
                        disabled={publishing}
                    />
                    {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
                    {!canPublish && <p className="text-sm text-yellow-400">Save your draft and resolve validation errors before publishing.</p>}
                    <div className="flex justify-end gap-2">
                        <Button type="button" size="sm" variant="ghost" disabled={publishing} onClick={() => setOpen(false)}>Cancel</Button>
                        <Button type="submit" size="sm" disabled={publishing || !canPublish} className="bg-teal-600 text-white hover:bg-teal-700">
                            {publishing ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <Rocket className="mr-2 h-4 w-4" />}
                            {publishing ? "Publishing…" : "Publish version"}
                        </Button>
                    </div>
                </form>
            </PopoverContent>
        </Popover>
    );
}
