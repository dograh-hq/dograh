'use client';

import { Upload } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useCallback, useState } from 'react';

import { createWorkflowApiV1WorkflowCreateDefinitionPost } from '@/client/sdk.gen';
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useAuth } from '@/lib/auth';
import logger from '@/lib/logger';
import { getRandomId } from '@/lib/utils';

import { WorkflowData } from '../flow/types';

export function UploadWorkflowButton() {
    const router = useRouter();
    const [isOpen, setIsOpen] = useState(false);
    const [isDragging, setIsDragging] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const { user, getAccessToken } = useAuth();

    const handleFileUpload = useCallback(async (file: File) => {
        try {
            const text = await file.text();
            const workflowData: WorkflowData = JSON.parse(text);

            if (!workflowData.workflow_definition?.nodes ||
                !workflowData.workflow_definition?.edges ||
                !workflowData.workflow_definition?.viewport) {
                throw new Error('Invalid workflow data structure');
            }

            if (!user) return;
            const accessToken = await getAccessToken();
            const response = await createWorkflowApiV1WorkflowCreateDefinitionPost({
                body: {
                    name: workflowData.name || `WF-${getRandomId()}`,
                    workflow_definition: workflowData.workflow_definition as unknown as { [key: string]: unknown },
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
            });

            if (response.data?.id) {
                router.push(`/workflow/${response.data.id}`);
                setIsOpen(false);
            }
        } catch (err) {
            setError('Failed to upload workflow. Please check if the file is valid.');
            logger.error(`Error uploading workflow: ${err}`);
        }
    }, [router, user, getAccessToken]);

    const handleDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);
        setError(null);

        const file = e.dataTransfer.files[0];
        if (file && file.type === 'application/json') {
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
