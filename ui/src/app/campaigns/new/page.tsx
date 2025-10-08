"use client";

import { ArrowLeft } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

import { createCampaignApiV1CampaignCreatePost, getWorkflowsSummaryApiV1WorkflowSummaryGet } from '@/client/sdk.gen';
import type { WorkflowSummaryResponse } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import { useAuth } from '@/lib/auth';

import CsvUploadSelector from '../CsvUploadSelector';
import GoogleSheetSelector from '../GoogleSheetSelector';

export default function NewCampaignPage() {
    const { user, getAccessToken, redirectToLogin, loading } = useAuth();
    const router = useRouter();

    // Form state
    const [campaignName, setCampaignName] = useState('');
    const [selectedWorkflowId, setSelectedWorkflowId] = useState<string>('');
    const [sourceType, setSourceType] = useState<'google-sheet' | 'csv'>('csv');
    const [sourceId, setSourceId] = useState('');
    const [selectedFileName, setSelectedFileName] = useState('');
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [userAccessToken, setUserAccessToken] = useState<string>('');

    // Workflows state
    const [workflows, setWorkflows] = useState<WorkflowSummaryResponse[]>([]);
    const [isLoadingWorkflows, setIsLoadingWorkflows] = useState(true);

    // Redirect if not authenticated
    useEffect(() => {
        if (!loading && !user) {
            redirectToLogin();
        }
    }, [loading, user, redirectToLogin]);

    // Fetch workflows
    const fetchWorkflows = useCallback(async () => {
        if (!user) return;
        try {
            const accessToken = await getAccessToken();
            setUserAccessToken(accessToken);
            const response = await getWorkflowsSummaryApiV1WorkflowSummaryGet({
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.data) {
                setWorkflows(response.data);
            }
        } catch (error) {
            console.error('Failed to fetch workflows:', error);
            toast.error('Failed to load workflows');
        } finally {
            setIsLoadingWorkflows(false);
        }
    }, [user, getAccessToken]);

    // Initial load
    useEffect(() => {
        if (user) {
            fetchWorkflows();
        }
    }, [fetchWorkflows, user]);

    // Handle form submission
    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();

        if (!campaignName || !selectedWorkflowId || !sourceId) {
            toast.error('Please fill in all fields');
            return;
        }

        setIsSubmitting(true);

        try {
            const accessToken = await getAccessToken();
            const response = await createCampaignApiV1CampaignCreatePost({
                body: {
                    name: campaignName,
                    workflow_id: parseInt(selectedWorkflowId),
                    source_type: sourceType,
                    source_id: sourceId,
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.data) {
                toast.success('Campaign created successfully');
                router.push(`/campaigns/${response.data.id}`);
            }
        } catch (error) {
            console.error('Failed to create campaign:', error);
            toast.error('Failed to create campaign');
        } finally {
            setIsSubmitting(false);
        }
    };

    // Handle back navigation
    const handleBack = () => {
        router.push('/campaigns');
    };

    // Handle sheet selection
    const handleSheetSelected = (sheetUrl: string) => {
        setSourceId(sheetUrl);
    };

    // Handle CSV file upload
    const handleFileUploaded = (fileKey: string, fileName: string) => {
        setSourceId(fileKey);
        setSelectedFileName(fileName);
    };

    return (
        <div className="container mx-auto p-6 space-y-6 max-w-2xl">
            <div>
                <Button
                    variant="ghost"
                    onClick={handleBack}
                    className="mb-4"
                >
                    <ArrowLeft className="h-4 w-4 mr-2" />
                    Back to Campaigns
                </Button>
                <h1 className="text-3xl font-bold text-gray-900 mb-2">Create New Campaign</h1>
                <p className="text-gray-600">Set up a new campaign to execute workflows at scale</p>
            </div>

            <Card>
                    <CardHeader>
                        <CardTitle>Campaign Details</CardTitle>
                        <CardDescription>
                            Configure your campaign settings
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        <form onSubmit={handleSubmit} className="space-y-6">
                            <div className="space-y-2">
                                <Label htmlFor="campaign-name">Campaign Name</Label>
                                <Input
                                    id="campaign-name"
                                    placeholder="Enter campaign name"
                                    value={campaignName}
                                    onChange={(e) => setCampaignName(e.target.value)}
                                    maxLength={255}
                                    required
                                />
                                <p className="text-sm text-gray-500">
                                    Choose a descriptive name for your campaign
                                </p>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="workflow">Workflow</Label>
                                <Select
                                    value={selectedWorkflowId}
                                    onValueChange={setSelectedWorkflowId}
                                    required
                                >
                                    <SelectTrigger id="workflow">
                                        <SelectValue placeholder="Select a workflow" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {isLoadingWorkflows ? (
                                            <SelectItem value="loading" disabled>
                                                Loading workflows...
                                            </SelectItem>
                                        ) : workflows.length === 0 ? (
                                            <SelectItem value="none" disabled>
                                                No workflows found
                                            </SelectItem>
                                        ) : (
                                            workflows.map((workflow) => (
                                                <SelectItem
                                                    key={workflow.id}
                                                    value={workflow.id.toString()}
                                                >
                                                    {workflow.name}
                                                </SelectItem>
                                            ))
                                        )}
                                    </SelectContent>
                                </Select>
                                <p className="text-sm text-gray-500">
                                    Select the workflow to execute for each row in the data source
                                </p>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="source-type">Data Source Type</Label>
                                <Select
                                    value={sourceType}
                                    onValueChange={(value) => {
                                        setSourceType(value as 'google-sheet' | 'csv');
                                        setSourceId('');
                                        setSelectedFileName('');
                                    }}
                                    required
                                >
                                    <SelectTrigger id="source-type">
                                        <SelectValue placeholder="Select source type" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="google-sheet">Google Sheet</SelectItem>
                                        <SelectItem value="csv">CSV File</SelectItem>
                                    </SelectContent>
                                </Select>
                                <p className="text-sm text-gray-500">
                                    Choose where your contact data is stored
                                </p>
                            </div>

                            {sourceType === 'google-sheet' ? (
                                <GoogleSheetSelector
                                    accessToken={userAccessToken}
                                    onSheetSelected={handleSheetSelected}
                                    selectedSheetUrl={sourceId}
                                />
                            ) : (
                                <CsvUploadSelector
                                    accessToken={userAccessToken}
                                    onFileUploaded={handleFileUploaded}
                                    selectedFileName={selectedFileName}
                                />
                            )}

                            <div className="flex gap-4 pt-4">
                                <Button
                                    type="submit"
                                    disabled={isSubmitting || !campaignName || !selectedWorkflowId || !sourceId}
                                >
                                    {isSubmitting ? 'Creating...' : 'Create Campaign'}
                                </Button>
                                <Button
                                    type="button"
                                    variant="outline"
                                    onClick={handleBack}
                                    disabled={isSubmitting}
                                >
                                    Cancel
                                </Button>
                            </div>
                        </form>
                    </CardContent>
                </Card>
        </div>
    );
}
