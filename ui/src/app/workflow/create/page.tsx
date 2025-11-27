'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { createWorkflowFromTemplateApiV1WorkflowCreateTemplatePost, createWorkflowRunApiV1WorkflowWorkflowIdRunsPost } from '@/client/sdk.gen';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { WORKFLOW_RUN_MODES } from '@/constants/workflowRunModes';
import { useAuth } from '@/lib/auth';
import logger from '@/lib/logger';
import { getRandomId } from '@/lib/utils';

export default function CreateWorkflowPage() {
    const router = useRouter();
    const { user, getAccessToken } = useAuth();
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [showSuccessModal, setShowSuccessModal] = useState(false);
    const [workflowId, setWorkflowId] = useState<string | null>(null);

    const [callType, setCallType] = useState<'INBOUND' | 'OUTBOUND'>('INBOUND');
    const [useCase, setUseCase] = useState('');
    const [activityDescription, setActivityDescription] = useState('');

    const handleCreateWorkflow = async () => {
        if (!useCase || !activityDescription) {
            setError('Please fill in all fields');
            return;
        }

        if (!user) {
            setError('You must be logged in to create a workflow');
            return;
        }

        setIsLoading(true);
        setError(null);

        try {
            const accessToken = await getAccessToken();

            // Call the API to create workflow from template
            const response = await createWorkflowFromTemplateApiV1WorkflowCreateTemplatePost({
                body: {
                    call_type: callType,
                    use_case: useCase,
                    activity_description: activityDescription,
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
            });

            if (response.data?.id) {
                setWorkflowId(String(response.data.id));
                setShowSuccessModal(true);
            }
        } catch (err) {
            setError('Failed to create workflow. Please try again.');
            logger.error(`Error creating workflow: ${err}`);
        } finally {
            setIsLoading(false);
        }
    };

    const handleModalContinue = async () => {
        if (!workflowId || !user) return;

        try {
            const accessToken = await getAccessToken();
            const workflowRunName = `WR-${getRandomId()}`;

            // Create a workflow run
            const response = await createWorkflowRunApiV1WorkflowWorkflowIdRunsPost({
                path: {
                    workflow_id: Number(workflowId),
                },
                body: {
                    mode: WORKFLOW_RUN_MODES.SMALL_WEBRTC, // Same mode as "Web Call" button
                    name: workflowRunName
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
            });

            // Navigate to the workflow run page
            if (response.data?.id) {
                router.push(`/workflow/${workflowId}/run/${response.data.id}`);
            }
        } catch (err) {
            logger.error(`Error creating workflow run: ${err}`);
            // Fallback to workflow page if run creation fails
            router.push(`/workflow/${workflowId}`);
        }
    };

    return (
        <div className="min-h-screen bg-background">
            <div className="container mx-auto px-4 py-8 max-w-2xl">
                <div className="mb-6">
                    <h1 className="text-3xl font-bold mb-2">Create Voice Agent</h1>
                    <p className="text-muted-foreground">
                        Tell us about your use case and we&apos;ll create a customized voice agent for you
                    </p>
                </div>

                <Card>
                    <CardHeader>
                        <CardTitle>Agent Details</CardTitle>
                        <CardDescription>
                            Configure your voice agent settings
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-6">
                        <div className="space-y-2">
                            <Label htmlFor="call-type">Call Type</Label>
                            <Select value={callType} onValueChange={(value) => setCallType(value as 'INBOUND' | 'OUTBOUND')}>
                                <SelectTrigger id="call-type">
                                    <SelectValue placeholder="Select type" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="INBOUND">
                                        Inbound (Users call AI)
                                    </SelectItem>
                                    <SelectItem value="OUTBOUND">
                                        Outbound (AI calls users)
                                    </SelectItem>
                                </SelectContent>
                            </Select>
                            <p className="text-sm text-muted-foreground">
                                Choose whether users will call your AI or your AI will call users
                            </p>
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="use-case">Use Case</Label>
                            <Input
                                id="use-case"
                                placeholder="e.g., Lead Qualification, HR Screening, Customer Support"
                                value={useCase}
                                onChange={(e) => setUseCase(e.target.value)}
                            />
                            <p className="text-sm text-muted-foreground">
                                Describe the primary purpose of your voice agent
                            </p>
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="activity-description">Activity Description</Label>
                            <Textarea
                                id="activity-description"
                                placeholder="Describe briefly what your voice agent will do (e.g., Qualify leads for real estate, Screen candidates for roles, Handle customer support). This will be a prompt to an LLM."
                                value={activityDescription}
                                onChange={(e) => setActivityDescription(e.target.value)}
                                className="min-h-[100px]"
                            />
                            <p className="text-sm text-muted-foreground">
                                This description will be used to generate the AI prompt for your voice agent
                            </p>
                        </div>

                        {error && (
                            <p className="text-sm text-red-500">{error}</p>
                        )}

                        <div className="pt-4">
                            <Button
                                onClick={handleCreateWorkflow}
                                disabled={isLoading || !useCase || !activityDescription}
                                className="w-full"
                            >
                                {isLoading ? 'Creating...' : 'Create Agent'}
                            </Button>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Loading Overlay */}
            {isLoading && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
                    <Card className="w-full max-w-md p-8">
                        <div className="flex flex-col items-center space-y-6">
                            {/* Animated spinner */}
                            <div className="relative">
                                <div className="w-16 h-16 border-4 border-muted rounded-full"></div>
                                <div className="absolute top-0 left-0 w-16 h-16 border-4 border-transparent border-t-primary rounded-full animate-spin"></div>
                            </div>

                            <div className="text-center space-y-2">
                                <h3 className="text-lg font-semibold">
                                    Creating Your Workflow
                                </h3>
                                <p className="text-sm text-muted-foreground max-w-xs">
                                    We&apos;re setting up your voice agent with your specifications. This will just take a moment...
                                </p>
                            </div>
                        </div>
                    </Card>
                </div>
            )}

            {/* Success Modal */}
            <Dialog open={showSuccessModal} onOpenChange={setShowSuccessModal}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <svg className="w-5 h-5 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                            Workflow Created Successfully!
                        </DialogTitle>
                        <DialogDescription asChild>
                            <div className="mt-4 space-y-3">
                                <p>
                                    A voice agent workflow has been generated for your use case, with some artificial data and sample actions.
                                </p>
                                <p>
                                    The voice bot is pre-set to communicate in English with an American accent.
                                </p>
                                <p>
                                    Next steps would be to test the voice bot using web call, and then modify it to suit your use case.
                                </p>
                            </div>
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter className="mt-6">
                        <Button
                            onClick={handleModalContinue}
                            className="w-full"
                        >
                            Start Web Call
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
