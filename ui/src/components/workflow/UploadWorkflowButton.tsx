'use client';

import { Upload } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useCallback, useState } from 'react';

import { createWorkflowApiV1WorkflowCreateDefinitionPost } from '@/client/sdk.gen';
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';
import logger from '@/lib/logger';
import { getRandomId } from '@/lib/utils';

export function UploadWorkflowButton() {
    const router = useRouter();
    const [isOpen, setIsOpen] = useState(false);
    const [isDragging, setIsDragging] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const { user, getAccessToken } = useAuth();

    const handleFileUpload = useCallback(async (file: File) => {
        setError(null);
        try {
            const raw = await file.text();
            let parsed: { name?: string; workflow_definition?: unknown; nodes?: unknown; edges?: unknown };
            try {
                parsed = JSON.parse(raw);
            } catch {
                setError("That file isn't valid JSON. Please upload a workflow JSON file.");
                return;
            }

            // Accept either { name, workflow_definition: {...} } or a raw
            // { nodes, edges } definition. viewport is optional — the editor
            // adds a default one, and AI-built / exported agents omit it.
            const definition = (parsed?.workflow_definition ?? parsed) as {
                nodes?: unknown;
                edges?: unknown;
            };
            if (!definition || !Array.isArray(definition.nodes) || !Array.isArray(definition.edges)) {
                setError('This doesn\'t look like an agent file — it must contain "nodes" and "edges".');
                return;
            }

            // An upload is a COPY of an agent. Strip trigger paths so the backend
            // mints fresh, unique ones — re-using the source agent's paths returns
            // a 409 Conflict (trigger endpoints must be unique per org).
            for (const node of definition.nodes as Array<{ type?: string; data?: Record<string, unknown> }>) {
                if (node?.type === 'trigger' && node.data && 'trigger_path' in node.data) {
                    delete node.data.trigger_path;
                }
            }

            if (!user) return;
            const accessToken = await getAccessToken();
            const response = await createWorkflowApiV1WorkflowCreateDefinitionPost({
                body: {
                    name: parsed?.name || `WF-${getRandomId()}`,
                    workflow_definition: definition as unknown as { [key: string]: unknown },
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
            });

            if (response.error) {
                setError(detailFromError(response.error, 'Failed to upload the agent.'));
                return;
            }
            if (response.data?.id) {
                router.push(`/workflow/${response.data.id}`);
                setIsOpen(false);
            } else {
                setError('Upload succeeded but no workflow was returned. Please try again.');
            }
        } catch (err) {
            setError('Failed to upload the agent. Please check the file and try again.');
            logger.error(`Error uploading workflow: ${err}`);
        }
    }, [router, user, getAccessToken]);

    const handleDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);
        setError(null);

        const file = e.dataTransfer.files[0];
        // Accept by MIME OR .json extension — browsers report .json files with
        // an empty or non-standard MIME type, which was rejecting valid files.
        if (file && (file.type === 'application/json' || file.name.toLowerCase().endsWith('.json'))) {
            handleFileUpload(file);
        } else {
            setError('Please upload a valid JSON file');
        }
    }, [handleFileUpload]);

    const handleDragOver = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(true);
    }, []);

    const handleDragLeave = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);
    }, []);

    const handleFileInput = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (file) {
            handleFileUpload(file);
        }
    }, [handleFileUpload]);

    return (
        <>
            <Button
                onClick={() => setIsOpen(true)}
                variant="outline"
            >
                <Upload className="w-4 h-4 mr-2" />
                Upload Agent Definition
            </Button>

            <Dialog open={isOpen} onOpenChange={setIsOpen}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>Upload Agent Definition</DialogTitle>
                    </DialogHeader>
                    <div
                        className={`mt-4 border-2 border-dashed rounded-lg p-8 text-center ${isDragging ? 'border-primary bg-primary/5' : 'border-gray-300'
                            }`}
                        onDrop={handleDrop}
                        onDragOver={handleDragOver}
                        onDragLeave={handleDragLeave}
                    >
                        <Upload className="w-8 h-8 mx-auto mb-4 text-gray-400" />
                        <p className="text-sm text-gray-600 mb-4">
                            Drag and drop your Workflow JSON File here, or Click to Select
                        </p>
                        <input
                            type="file"
                            accept=".json"
                            onChange={handleFileInput}
                            className="hidden"
                            id="workflow-upload"
                        />
                        <Button
                            variant="outline"
                            onClick={() => document.getElementById('workflow-upload')?.click()}
                        >
                            Select File
                        </Button>
                        {error && (
                            <p className="mt-4 text-sm text-red-600">{error}</p>
                        )}
                    </div>
                </DialogContent>
            </Dialog>
        </>
    );
}
