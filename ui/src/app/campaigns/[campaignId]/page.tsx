"use client";

import { ArrowLeft, Pause, Play, RefreshCw } from 'lucide-react';
import { useParams, useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

import {
    getCampaignApiV1CampaignCampaignIdGet,
    getCampaignRunsApiV1CampaignCampaignIdRunsGet,
    pauseCampaignApiV1CampaignCampaignIdPausePost,
    resumeCampaignApiV1CampaignCampaignIdResumePost,
    startCampaignApiV1CampaignCampaignIdStartPost} from '@/client/sdk.gen';
import type { CampaignResponse, WorkflowRunResponse } from '@/client/types.gen';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table';
import { useAuth } from '@/lib/auth';

export default function CampaignDetailPage() {
    const { user, getAccessToken, redirectToLogin, loading } = useAuth();
    const router = useRouter();
    const params = useParams();
    const campaignId = parseInt(params.campaignId as string);

    // Redirect if not authenticated
    useEffect(() => {
        if (!loading && !user) {
            redirectToLogin();
        }
    }, [loading, user, redirectToLogin]);

    // Campaign state
    const [campaign, setCampaign] = useState<CampaignResponse | null>(null);
    const [isLoadingCampaign, setIsLoadingCampaign] = useState(true);

    // Runs state
    const [runs, setRuns] = useState<WorkflowRunResponse[]>([]);
    const [isLoadingRuns, setIsLoadingRuns] = useState(false);

    // Action state
    const [isExecutingAction, setIsExecutingAction] = useState(false);

    // Fetch campaign details
    const fetchCampaign = useCallback(async () => {
        if (!user) return;
        setIsLoadingCampaign(true);
        try {
            const accessToken = await getAccessToken();
            const response = await getCampaignApiV1CampaignCampaignIdGet({
                path: {
                    campaign_id: campaignId,
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.data) {
                setCampaign(response.data);
            }
        } catch (error) {
            console.error('Failed to fetch campaign:', error);
            toast.error('Failed to load campaign details');
        } finally {
            setIsLoadingCampaign(false);
        }
    }, [user, getAccessToken, campaignId]);

    // Fetch campaign runs
    const fetchCampaignRuns = useCallback(async () => {
        if (!user) return;
        setIsLoadingRuns(true);
        try {
            const accessToken = await getAccessToken();
            const response = await getCampaignRunsApiV1CampaignCampaignIdRunsGet({
                path: {
                    campaign_id: campaignId,
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.data) {
                setRuns(response.data);
            }
        } catch (error) {
            console.error('Failed to fetch campaign runs:', error);
        } finally {
            setIsLoadingRuns(false);
        }
    }, [user, getAccessToken, campaignId]);

    // Initial load
    useEffect(() => {
        fetchCampaign();
        fetchCampaignRuns();
    }, [fetchCampaign, fetchCampaignRuns]);

    // Handle back navigation
    const handleBack = () => {
        router.push('/campaigns');
    };

    // Handle workflow link click
    const handleWorkflowClick = () => {
        if (campaign) {
            router.push(`/workflow/${campaign.workflow_id}`);
        }
    };

    // Handle run click
    const handleRunClick = (runId: number) => {
        if (campaign) {
            router.push(`/workflow/${campaign.workflow_id}/run/${runId}`);
        }
    };

    // Handle start campaign
    const handleStart = async () => {
        if (!user) return;
        setIsExecutingAction(true);
        try {
            const accessToken = await getAccessToken();
            const response = await startCampaignApiV1CampaignCampaignIdStartPost({
                path: {
                    campaign_id: campaignId,
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.data) {
                setCampaign(response.data);
                toast.success('Campaign started');
            } else if (response.error) {
                // Extract error message from response
                let errorMsg = 'Failed to start campaign';
                if (typeof response.error === 'string') {
                    errorMsg = response.error;
                } else if (response.error && typeof response.error === 'object') {
                    errorMsg = (response.error as unknown as { detail?: string }).detail || JSON.stringify(response.error);
                }
                toast.error(errorMsg);
            }
        } catch (error) {
            console.error('Failed to start campaign:', error);
            toast.error('Failed to start campaign');
        } finally {
            setIsExecutingAction(false);
        }
    };

    // Handle resume campaign
    const handleResume = async () => {
        if (!user) return;
        setIsExecutingAction(true);
        try {
            const accessToken = await getAccessToken();
            const response = await resumeCampaignApiV1CampaignCampaignIdResumePost({
                path: {
                    campaign_id: campaignId,
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.data) {
                setCampaign(response.data);
                toast.success('Campaign resumed');
            } else if (response.error) {
                // Extract error message from response
                let errorMsg = 'Failed to resume campaign';
                if (typeof response.error === 'string') {
                    errorMsg = response.error;
                } else if (response.error && typeof response.error === 'object') {
                    errorMsg = (response.error as unknown as { detail?: string }).detail || JSON.stringify(response.error);
                }
                toast.error(errorMsg);
            }
        } catch (error) {
            console.error('Failed to resume campaign:', error);
            toast.error('Failed to resume campaign');
        } finally {
            setIsExecutingAction(false);
        }
    };

    // Handle pause campaign
    const handlePause = async () => {
        if (!user) return;
        setIsExecutingAction(true);
        try {
            const accessToken = await getAccessToken();
            const response = await pauseCampaignApiV1CampaignCampaignIdPausePost({
                path: {
                    campaign_id: campaignId,
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.data) {
                setCampaign(response.data);
                toast.success('Campaign paused');
            }
        } catch (error) {
            console.error('Failed to pause campaign:', error);
            toast.error('Failed to pause campaign');
        } finally {
            setIsExecutingAction(false);
        }
    };

    // Format date for display
    const formatDate = (dateString: string) => {
        return new Date(dateString).toLocaleDateString();
    };

    const formatDateTime = (dateString: string) => {
        return new Date(dateString).toLocaleString();
    };

    // Get badge variant for state
    const getStateBadgeVariant = (state: string) => {
        switch (state) {
            case 'created':
                return 'secondary';
            case 'running':
                return 'default';
            case 'paused':
                return 'outline';
            case 'completed':
                return 'secondary';
            case 'failed':
                return 'destructive';
            default:
                return 'secondary';
        }
    };

    // Render action button based on state
    const renderActionButton = () => {
        if (!campaign || isExecutingAction) return null;

        switch (campaign.state) {
            case 'created':
                return (
                    <Button onClick={handleStart} disabled={isExecutingAction}>
                        <Play className="h-4 w-4 mr-2" />
                        Start Campaign
                    </Button>
                );
            case 'running':
                return (
                    <Button onClick={handlePause} disabled={isExecutingAction}>
                        <Pause className="h-4 w-4 mr-2" />
                        Pause Campaign
                    </Button>
                );
            case 'paused':
                return (
                    <Button onClick={handleResume} disabled={isExecutingAction}>
                        <RefreshCw className="h-4 w-4 mr-2" />
                        Resume Campaign
                    </Button>
                );
            default:
                return null;
        }
    };

    if (isLoadingCampaign) {
        return (
            <div className="container mx-auto p-6 space-y-6">
                <div className="animate-pulse">
                    <div className="h-8 bg-gray-200 rounded w-1/4 mb-4"></div>
                    <div className="h-64 bg-gray-200 rounded"></div>
                </div>
            </div>
        );
    }

    if (!campaign) {
        return (
            <div className="container mx-auto p-6 space-y-6">
                <p className="text-center text-gray-500">Campaign not found</p>
            </div>
        );
    }

    return (
        <div className="container mx-auto p-6 space-y-6">
            <div>
                <Button
                    variant="ghost"
                    onClick={handleBack}
                    className="mb-4"
                >
                    <ArrowLeft className="h-4 w-4 mr-2" />
                    Back to Campaigns
                </Button>
                <div className="flex justify-between items-start">
                    <div>
                        <h1 className="text-3xl font-bold text-gray-900 mb-2">{campaign.name}</h1>
                            <div className="flex items-center gap-4">
                                <Badge variant={getStateBadgeVariant(campaign.state)}>
                                    {campaign.state}
                                </Badge>
                                <span className="text-gray-600">
                                    Created {formatDate(campaign.created_at)}
                                </span>
                            </div>
                        </div>
                        {renderActionButton()}
                    </div>
                </div>

                {/* Campaign Details */}
                <Card className="mb-6">
                    <CardHeader>
                        <CardTitle>Campaign Details</CardTitle>
                        <CardDescription>
                            Configuration and source information
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        <dl className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <dt className="text-sm font-medium text-gray-500">Workflow</dt>
                                <dd className="mt-1">
                                    <button
                                        onClick={handleWorkflowClick}
                                        className="text-blue-600 hover:text-blue-800 hover:underline"
                                    >
                                        {campaign.workflow_name}
                                    </button>
                                </dd>
                            </div>
                            <div>
                                <dt className="text-sm font-medium text-gray-500">Source Type</dt>
                                <dd className="mt-1 capitalize">{campaign.source_type.replace('-', ' ')}</dd>
                            </div>
                            <div>
                                <dt className="text-sm font-medium text-gray-500">Source Sheet</dt>
                                <dd className="mt-1">
                                    <a
                                        href={campaign.source_id}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="text-blue-600 hover:text-blue-800 hover:underline text-sm break-all"
                                    >
                                        {campaign.source_id}
                                    </a>
                                </dd>
                            </div>
                            <div>
                                <dt className="text-sm font-medium text-gray-500">State</dt>
                                <dd className="mt-1 capitalize">{campaign.state}</dd>
                            </div>
                            {campaign.started_at && (
                                <div>
                                    <dt className="text-sm font-medium text-gray-500">Started At</dt>
                                    <dd className="mt-1">{formatDateTime(campaign.started_at)}</dd>
                                </div>
                            )}
                            {campaign.completed_at && (
                                <div>
                                    <dt className="text-sm font-medium text-gray-500">Completed At</dt>
                                    <dd className="mt-1">{formatDateTime(campaign.completed_at)}</dd>
                                </div>
                            )}
                        </dl>
                    </CardContent>
                </Card>

                {/* Workflow Runs */}
                <Card>
                    <CardHeader>
                        <CardTitle>Workflow Runs</CardTitle>
                        <CardDescription>
                            Executions triggered by this campaign
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        {isLoadingRuns ? (
                            <div className="animate-pulse space-y-3">
                                {[...Array(3)].map((_, i) => (
                                    <div key={i} className="h-12 bg-gray-200 rounded"></div>
                                ))}
                            </div>
                        ) : runs.length > 0 ? (
                            <div className="overflow-x-auto">
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Run ID</TableHead>
                                            <TableHead>State</TableHead>
                                            <TableHead>Created</TableHead>
                                            <TableHead className="text-right">Action</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {runs.map((run) => (
                                            <TableRow
                                                key={run.id}
                                                className="cursor-pointer hover:bg-gray-50"
                                                onClick={() => handleRunClick(run.id)}
                                            >
                                                <TableCell className="font-mono text-sm">#{run.id}</TableCell>
                                                <TableCell>
                                                    <Badge variant={run.state === 'completed' ? 'secondary' : 'default'}>
                                                        {run.state}
                                                    </Badge>
                                                </TableCell>
                                                <TableCell>{formatDateTime(run.created_at)}</TableCell>
                                                <TableCell className="text-right">
                                                    <Button
                                                        variant="outline"
                                                        size="sm"
                                                        onClick={(e) => {
                                                            e.stopPropagation();
                                                            handleRunClick(run.id);
                                                        }}
                                                    >
                                                        View
                                                    </Button>
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </div>
                        ) : (
                            <p className="text-center py-8 text-gray-500">
                                {campaign.state === 'created'
                                    ? 'No runs yet. Start the campaign to begin execution.'
                                    : 'No workflow runs found for this campaign.'}
                            </p>
                        )}
                    </CardContent>
                </Card>
        </div>
    );
}
