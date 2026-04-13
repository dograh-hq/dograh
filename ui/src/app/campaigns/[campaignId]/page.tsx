"use client";

import { format } from 'date-fns';
import { ArrowLeft, CalendarIcon, Check, Clock, Download, Pause, Pencil, Phone, Play, RefreshCw, X } from 'lucide-react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

import {
    downloadCampaignReportApiV1CampaignCampaignIdReportGet,
    getCampaignApiV1CampaignCampaignIdGet,
    getCampaignSourceDownloadUrlApiV1CampaignCampaignIdSourceDownloadUrlGet,
    pauseCampaignApiV1CampaignCampaignIdPausePost,
    redialCampaignApiV1CampaignCampaignIdRedialPost,
    resumeCampaignApiV1CampaignCampaignIdResumePost,
    startCampaignApiV1CampaignCampaignIdStartPost,
} from '@/client/sdk.gen';
import type { CampaignResponse } from '@/client/types.gen';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Separator } from '@/components/ui/separator';
import { CampaignRuns } from '@/components/workflow-runs';
import { useAuth } from '@/lib/auth';

export default function CampaignDetailPage() {
    const { user, getAccessToken, redirectToLogin, loading } = useAuth();
    const router = useRouter();
    const params = useParams();
    const searchParams = useSearchParams();
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

    // Action state
    const [isExecutingAction, setIsExecutingAction] = useState(false);
    const [isDownloadingReport, setIsDownloadingReport] = useState(false);

    // Report date range state
    const [reportStartDate, setReportStartDate] = useState<Date | undefined>(undefined);
    const [reportStartTime, setReportStartTime] = useState('00:00');
    const [reportEndDate, setReportEndDate] = useState<Date | undefined>(undefined);
    const [reportEndTime, setReportEndTime] = useState('23:59');
    const [isReportPopoverOpen, setIsReportPopoverOpen] = useState(false);

    // Redial dialog state
    const [isRedialDialogOpen, setIsRedialDialogOpen] = useState(false);
    const [redialName, setRedialName] = useState('');
    const [redialOnVoicemail, setRedialOnVoicemail] = useState(true);
    const [redialOnNoAnswer, setRedialOnNoAnswer] = useState(true);
    const [redialOnBusy, setRedialOnBusy] = useState(true);
    const [isRedialing, setIsRedialing] = useState(false);

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

    // Initial load
    useEffect(() => {
        fetchCampaign();
    }, [fetchCampaign]);

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

    // Handle CSV download
    const handleDownloadCsv = async () => {
        if (!user || !campaign || campaign.source_type !== 'csv') return;

        try {
            const accessToken = await getAccessToken();
            const response = await getCampaignSourceDownloadUrlApiV1CampaignCampaignIdSourceDownloadUrlGet({
                path: {
                    campaign_id: campaignId,
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.data?.download_url) {
                // Open download URL in new tab
                window.open(response.data.download_url, '_blank');
            } else {
                toast.error('Failed to get download URL');
            }
        } catch (error) {
            console.error('Failed to download CSV:', error);
            toast.error('Failed to download CSV file');
        }
    };

    // Build ISO datetime string from date + time
    const buildDateTime = (date: Date | undefined, time: string): string | undefined => {
        if (!date) return undefined;
        const [hours, minutes] = time.split(':').map(Number);
        const combined = new Date(date);
        combined.setHours(hours, minutes, 0, 0);
        return combined.toISOString();
    };

    // Handle download report
    const handleDownloadReport = async () => {
        if (!user) return;
        setIsDownloadingReport(true);
        setIsReportPopoverOpen(false);
        try {
            const accessToken = await getAccessToken();
            const startDate = buildDateTime(reportStartDate, reportStartTime);
            const endDate = buildDateTime(reportEndDate, reportEndTime);

            const response = await downloadCampaignReportApiV1CampaignCampaignIdReportGet({
                path: {
                    campaign_id: campaignId,
                },
                query: {
                    start_date: startDate,
                    end_date: endDate,
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
                parseAs: 'blob',
            });

            if (response.data) {
                const blob = response.data as Blob;
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `campaign_${campaignId}_report.csv`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                window.URL.revokeObjectURL(url);
            } else {
                toast.error('Failed to download report');
            }
        } catch (error) {
            console.error('Failed to download report:', error);
            toast.error('Failed to download report');
        } finally {
            setIsDownloadingReport(false);
        }
    };

    const handleClearDateRange = () => {
        setReportStartDate(undefined);
        setReportStartTime('00:00');
        setReportEndDate(undefined);
        setReportEndTime('23:59');
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

    // Open redial dialog with default name
    const openRedialDialog = () => {
        if (!campaign) return;
        setRedialName(`${campaign.name} (Redial)`);
        setRedialOnVoicemail(true);
        setRedialOnNoAnswer(true);
        setRedialOnBusy(true);
        setIsRedialDialogOpen(true);
    };

    // Handle redial campaign
    const handleRedial = async () => {
        if (!user || !campaign) return;
        if (!redialOnVoicemail && !redialOnNoAnswer && !redialOnBusy) {
            toast.error('Select at least one reason to redial');
            return;
        }
        setIsRedialing(true);
        try {
            const accessToken = await getAccessToken();
            const response = await redialCampaignApiV1CampaignCampaignIdRedialPost({
                path: {
                    campaign_id: campaignId,
                },
                body: {
                    name: redialName || null,
                    retry_on_voicemail: redialOnVoicemail,
                    retry_on_no_answer: redialOnNoAnswer,
                    retry_on_busy: redialOnBusy,
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.data) {
                toast.success('Redial campaign created');
                setIsRedialDialogOpen(false);
                router.push(`/campaigns/${response.data.id}`);
            } else if (response.error) {
                let errorMsg = 'Failed to create redial campaign';
                if (typeof response.error === 'string') {
                    errorMsg = response.error;
                } else if (response.error && typeof response.error === 'object') {
                    errorMsg = (response.error as unknown as { detail?: string }).detail || JSON.stringify(response.error);
                }
                toast.error(errorMsg);
            }
        } catch (error) {
            console.error('Failed to redial campaign:', error);
            toast.error('Failed to create redial campaign');
        } finally {
            setIsRedialing(false);
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

    const canEdit = campaign && ['created', 'running', 'paused'].includes(campaign.state);

    // Render action button based on state
    const renderActionButton = () => {
        if (!campaign || isExecutingAction) return null;

        const editButton = canEdit ? (
            <Button variant="outline" onClick={() => router.push(`/campaigns/${campaignId}/edit`)}>
                <Pencil className="h-4 w-4 mr-2" />
                Edit Campaign
            </Button>
        ) : null;

        switch (campaign.state) {
            case 'created':
                return (
                    <div className="flex items-center gap-2">
                        {editButton}
                        <Button onClick={handleStart} disabled={isExecutingAction}>
                            <Play className="h-4 w-4 mr-2" />
                            Start Campaign
                        </Button>
                    </div>
                );
            case 'running':
                return (
                    <div className="flex items-center gap-2">
                        {editButton}
                        <Button onClick={handlePause} disabled={isExecutingAction}>
                            <Pause className="h-4 w-4 mr-2" />
                            Pause Campaign
                        </Button>
                    </div>
                );
            case 'paused':
                return (
                    <div className="flex items-center gap-2">
                        {editButton}
                        <Button onClick={handleResume} disabled={isExecutingAction}>
                            <RefreshCw className="h-4 w-4 mr-2" />
                            Resume Campaign
                        </Button>
                    </div>
                );
            case 'completed':
                if (campaign.redialed_campaign_id) {
                    return null;
                }
                return (
                    <Button onClick={openRedialDialog}>
                        <Phone className="h-4 w-4 mr-2" />
                        Redial Campaign
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
                    <div className="h-8 bg-muted rounded w-1/4 mb-4"></div>
                    <div className="h-64 bg-muted rounded"></div>
                </div>
            </div>
        );
    }

    if (!campaign) {
        return (
            <div className="container mx-auto p-6 space-y-6">
                <p className="text-center text-muted-foreground">Campaign not found</p>
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
                        <h1 className="text-3xl font-bold mb-2">{campaign.name}</h1>
                            <div className="flex items-center gap-4">
                                <Badge variant={getStateBadgeVariant(campaign.state)}>
                                    {campaign.state}
                                </Badge>
                                <span className="text-muted-foreground">
                                    Created {formatDate(campaign.created_at)}
                                </span>
                            </div>
                        </div>
                        <div className="flex items-center gap-2">
                            <Popover open={isReportPopoverOpen} onOpenChange={setIsReportPopoverOpen}>
                                <PopoverTrigger asChild>
                                    <Button variant="outline" disabled={isDownloadingReport}>
                                        <Download className="h-4 w-4 mr-2" />
                                        Download Report
                                    </Button>
                                </PopoverTrigger>
                                <PopoverContent className="w-auto p-4" align="end">
                                    <div className="space-y-4">
                                        <div className="text-sm font-medium">Filter by date range</div>
                                        <div className="grid gap-3">
                                            <div className="space-y-1.5">
                                                <Label className="text-xs">From</Label>
                                                <div className="flex gap-2">
                                                    <Popover>
                                                        <PopoverTrigger asChild>
                                                            <Button variant="outline" size="sm" className="w-[140px] justify-start text-left font-normal">
                                                                <CalendarIcon className="mr-2 h-3.5 w-3.5" />
                                                                {reportStartDate ? format(reportStartDate, 'MMM dd, yyyy') : 'Start date'}
                                                            </Button>
                                                        </PopoverTrigger>
                                                        <PopoverContent className="w-auto p-0" align="start">
                                                            <Calendar
                                                                mode="single"
                                                                selected={reportStartDate}
                                                                onSelect={setReportStartDate}
                                                                disabled={(date) => reportEndDate ? date > reportEndDate : false}
                                                            />
                                                        </PopoverContent>
                                                    </Popover>
                                                    <Input
                                                        type="time"
                                                        value={reportStartTime}
                                                        onChange={(e) => setReportStartTime(e.target.value)}
                                                        className="w-[100px] h-8 text-xs"
                                                    />
                                                </div>
                                            </div>
                                            <div className="space-y-1.5">
                                                <Label className="text-xs">To</Label>
                                                <div className="flex gap-2">
                                                    <Popover>
                                                        <PopoverTrigger asChild>
                                                            <Button variant="outline" size="sm" className="w-[140px] justify-start text-left font-normal">
                                                                <CalendarIcon className="mr-2 h-3.5 w-3.5" />
                                                                {reportEndDate ? format(reportEndDate, 'MMM dd, yyyy') : 'End date'}
                                                            </Button>
                                                        </PopoverTrigger>
                                                        <PopoverContent className="w-auto p-0" align="start">
                                                            <Calendar
                                                                mode="single"
                                                                selected={reportEndDate}
                                                                onSelect={setReportEndDate}
                                                                disabled={(date) => reportStartDate ? date < reportStartDate : false}
                                                            />
                                                        </PopoverContent>
                                                    </Popover>
                                                    <Input
                                                        type="time"
                                                        value={reportEndTime}
                                                        onChange={(e) => setReportEndTime(e.target.value)}
                                                        className="w-[100px] h-8 text-xs"
                                                    />
                                                </div>
                                            </div>
                                        </div>
                                        <Separator />
                                        <div className="flex justify-between">
                                            <Button variant="ghost" size="sm" onClick={handleClearDateRange}>
                                                Clear
                                            </Button>
                                            <Button size="sm" onClick={handleDownloadReport} disabled={isDownloadingReport}>
                                                <Download className="h-3.5 w-3.5 mr-1.5" />
                                                {reportStartDate || reportEndDate ? 'Download Filtered' : 'Download All'}
                                            </Button>
                                        </div>
                                    </div>
                                </PopoverContent>
                            </Popover>
                            {renderActionButton()}
                        </div>
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
                                <dt className="text-sm font-medium">Workflow</dt>
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
                                <dt className="text-sm font-medium">Source Type</dt>
                                <dd className="mt-1 capitalize">{campaign.source_type.replace('-', ' ')}</dd>
                            </div>
                            <div>
                                <dt className="text-sm font-medium">
                                    {campaign.source_type === 'csv' ? 'Source File' : 'Source Sheet'}
                                </dt>
                                <dd className="mt-1">
                                    {campaign.source_type === 'csv' ? (
                                        <button
                                            onClick={handleDownloadCsv}
                                            className="text-blue-600 hover:text-blue-800 hover:underline text-sm break-all"
                                        >
                                            {campaign.source_id.split('/').pop()}
                                        </button>
                                    ) : (
                                        <a
                                            href={campaign.source_id}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="text-blue-600 hover:text-blue-800 hover:underline text-sm break-all"
                                        >
                                            {campaign.source_id}
                                        </a>
                                    )}
                                </dd>
                            </div>
                            <div>
                                <dt className="text-sm font-medium">State</dt>
                                <dd className="mt-1 capitalize">{campaign.state}</dd>
                            </div>
                            <div>
                                <dt className="text-sm font-medium">Progress</dt>
                                <dd className="mt-1">
                                    {campaign.executed_count} / {campaign.total_queued_count}
                                </dd>
                            </div>
                            {campaign.parent_campaign_id && (
                                <div>
                                    <dt className="text-sm font-medium">Redial Of</dt>
                                    <dd className="mt-1">
                                        <button
                                            onClick={() => router.push(`/campaigns/${campaign.parent_campaign_id}`)}
                                            className="text-blue-600 hover:text-blue-800 hover:underline"
                                        >
                                            Campaign #{campaign.parent_campaign_id}
                                        </button>
                                    </dd>
                                </div>
                            )}
                            {campaign.redialed_campaign_id && (
                                <div>
                                    <dt className="text-sm font-medium">Redialed As</dt>
                                    <dd className="mt-1">
                                        <button
                                            onClick={() => router.push(`/campaigns/${campaign.redialed_campaign_id}`)}
                                            className="text-blue-600 hover:text-blue-800 hover:underline"
                                        >
                                            Campaign #{campaign.redialed_campaign_id}
                                        </button>
                                    </dd>
                                </div>
                            )}
                            {campaign.started_at && (
                                <div>
                                    <dt className="text-sm font-medium">Started At</dt>
                                    <dd className="mt-1">{formatDateTime(campaign.started_at)}</dd>
                                </div>
                            )}
                            {campaign.completed_at && (
                                <div>
                                    <dt className="text-sm font-medium">Completed At</dt>
                                    <dd className="mt-1">{formatDateTime(campaign.completed_at)}</dd>
                                </div>
                            )}
                        </dl>
                    </CardContent>
                </Card>

                {/* Campaign Settings */}
                <Card className="mb-6">
                    <CardHeader>
                        <CardTitle>Campaign Settings</CardTitle>
                        <CardDescription>
                            Concurrency and retry configuration
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-6">
                        {/* Concurrency Setting */}
                        <div>
                            <dt className="text-sm font-medium">Max Concurrent Calls</dt>
                            <dd className="mt-1">
                                {campaign.max_concurrency ? (
                                    <span>{campaign.max_concurrency}</span>
                                ) : (
                                    <span className="text-muted-foreground">Using organization default</span>
                                )}
                            </dd>
                        </div>

                        <Separator />

                        {/* Retry Configuration */}
                        <div className="space-y-4">
                            <div className="flex items-center justify-between">
                                <span className="text-sm font-medium">Retries Enabled</span>
                                {campaign.retry_config.enabled ? (
                                    <Badge variant="default" className="flex items-center gap-1">
                                        <Check className="h-3 w-3" />
                                        Enabled
                                    </Badge>
                                ) : (
                                    <Badge variant="secondary" className="flex items-center gap-1">
                                        <X className="h-3 w-3" />
                                        Disabled
                                    </Badge>
                                )}
                            </div>

                            {campaign.retry_config.enabled && (
                                <div className="grid grid-cols-2 md:grid-cols-3 gap-4 pl-4 border-l-2 border-muted">
                                    <div>
                                        <dt className="text-sm text-muted-foreground">Max Retries</dt>
                                        <dd className="mt-1 font-medium">{campaign.retry_config.max_retries}</dd>
                                    </div>
                                    <div>
                                        <dt className="text-sm text-muted-foreground">Retry Delay</dt>
                                        <dd className="mt-1 font-medium">{campaign.retry_config.retry_delay_seconds}s</dd>
                                    </div>
                                    <div className="col-span-2 md:col-span-1">
                                        <dt className="text-sm text-muted-foreground">Retry On</dt>
                                        <dd className="mt-1 flex flex-wrap gap-1">
                                            {campaign.retry_config.retry_on_busy && (
                                                <Badge variant="outline" className="text-xs">Busy</Badge>
                                            )}
                                            {campaign.retry_config.retry_on_no_answer && (
                                                <Badge variant="outline" className="text-xs">No Answer</Badge>
                                            )}
                                            {campaign.retry_config.retry_on_voicemail && (
                                                <Badge variant="outline" className="text-xs">Voicemail</Badge>
                                            )}
                                        </dd>
                                    </div>
                                </div>
                            )}
                        </div>

                        <Separator />

                        {/* Call Schedule (read-only) */}
                        <div className="space-y-4">
                            <div className="flex items-center justify-between">
                                <span className="text-sm font-medium">Call Schedule</span>
                                <div className="flex items-center gap-2">
                                    {campaign.schedule_config?.enabled ? (
                                        <Badge variant="default" className="flex items-center gap-1">
                                            <Clock className="h-3 w-3" />
                                            Enabled
                                        </Badge>
                                    ) : (
                                        <Badge variant="secondary" className="flex items-center gap-1">
                                            <X className="h-3 w-3" />
                                            Not configured
                                        </Badge>
                                    )}
                                </div>
                            </div>

                            {campaign.schedule_config?.enabled && (
                                <div className="pl-4 border-l-2 border-muted space-y-3">
                                    <div>
                                        <dt className="text-sm text-muted-foreground">Timezone</dt>
                                        <dd className="mt-1 font-medium">{campaign.schedule_config.timezone.replace(/_/g, ' ')}</dd>
                                    </div>
                                    <div>
                                        <dt className="text-sm text-muted-foreground">Time Slots</dt>
                                        <dd className="mt-1 flex flex-wrap gap-2">
                                            {campaign.schedule_config.slots.map((slot, index) => {
                                                const dayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
                                                return (
                                                    <div key={index} className="flex items-center gap-1">
                                                        <Badge variant="outline" className="text-xs">{dayNames[slot.day_of_week]}</Badge>
                                                        <span className="text-sm">{slot.start_time} - {slot.end_time}</span>
                                                    </div>
                                                );
                                            })}
                                        </dd>
                                    </div>
                                </div>
                            )}
                        </div>
                    </CardContent>
                </Card>

                {/* Workflow Runs */}
                <CampaignRuns
                    campaignId={campaignId}
                    workflowId={campaign.workflow_id}
                    searchParams={searchParams}
                />

                <Dialog open={isRedialDialogOpen} onOpenChange={setIsRedialDialogOpen}>
                    <DialogContent>
                        <DialogHeader>
                            <DialogTitle>Redial Campaign</DialogTitle>
                            <DialogDescription>
                                Creates a new campaign that re-dials unique subscribers whose
                                last call ended with one of the selected outcomes. Subscribers
                                who were successfully reached on a retry are skipped.
                            </DialogDescription>
                        </DialogHeader>
                        <div className="space-y-4 py-2">
                            <div className="space-y-1.5">
                                <Label htmlFor="redial-name">Name</Label>
                                <Input
                                    id="redial-name"
                                    value={redialName}
                                    onChange={(e) => setRedialName(e.target.value)}
                                    placeholder="Campaign name"
                                />
                            </div>
                            <div className="space-y-3">
                                <Label>Redial when last call was</Label>
                                <div className="flex items-center gap-2">
                                    <Checkbox
                                        id="redial-voicemail"
                                        checked={redialOnVoicemail}
                                        onCheckedChange={(v) => setRedialOnVoicemail(v === true)}
                                    />
                                    <Label htmlFor="redial-voicemail" className="font-normal">
                                        Voicemail
                                    </Label>
                                </div>
                                <div className="flex items-center gap-2">
                                    <Checkbox
                                        id="redial-no-answer"
                                        checked={redialOnNoAnswer}
                                        onCheckedChange={(v) => setRedialOnNoAnswer(v === true)}
                                    />
                                    <Label htmlFor="redial-no-answer" className="font-normal">
                                        No Answer
                                    </Label>
                                </div>
                                <div className="flex items-center gap-2">
                                    <Checkbox
                                        id="redial-busy"
                                        checked={redialOnBusy}
                                        onCheckedChange={(v) => setRedialOnBusy(v === true)}
                                    />
                                    <Label htmlFor="redial-busy" className="font-normal">
                                        Busy
                                    </Label>
                                </div>
                            </div>
                        </div>
                        <DialogFooter>
                            <Button
                                variant="outline"
                                onClick={() => setIsRedialDialogOpen(false)}
                                disabled={isRedialing}
                            >
                                Cancel
                            </Button>
                            <Button onClick={handleRedial} disabled={isRedialing}>
                                {isRedialing ? 'Creating...' : 'Create Redial Campaign'}
                            </Button>
                        </DialogFooter>
                    </DialogContent>
                </Dialog>
        </div>
    );
}
