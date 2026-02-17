"use client";

import { ArrowLeft, ChevronDown, ChevronRight } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import type { ITimezoneOption } from 'react-timezone-select';
import { toast } from 'sonner';

import {
    createCampaignApiV1CampaignCreatePost,
    getCampaignLimitsApiV1OrganizationsCampaignLimitsGet,
    getWorkflowsSummaryApiV1WorkflowSummaryGet
} from '@/client/sdk.gen';
import type { WorkflowSummaryResponse } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
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

import CampaignAdvancedSettings, { getTimezoneValue, type TimeSlot } from '../CampaignAdvancedSettings';
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
    const [createError, setCreateError] = useState<string | null>(null);
    const [userAccessToken, setUserAccessToken] = useState<string>('');

    // Workflows state
    const [workflows, setWorkflows] = useState<WorkflowSummaryResponse[]>([]);
    const [isLoadingWorkflows, setIsLoadingWorkflows] = useState(true);

    // Advanced settings state
    const [showAdvancedSettings, setShowAdvancedSettings] = useState(false);
    const [orgConcurrentLimit, setOrgConcurrentLimit] = useState<number>(2);
    const [fromNumbersCount, setFromNumbersCount] = useState<number>(0);
    const [maxConcurrency, setMaxConcurrency] = useState<string>('');
    // Retry config state
    const [retryEnabled, setRetryEnabled] = useState(true);
    const [maxRetries, setMaxRetries] = useState<string>('2');
    const [retryDelaySeconds, setRetryDelaySeconds] = useState<string>('120');
    const [retryOnBusy, setRetryOnBusy] = useState(true);
    const [retryOnNoAnswer, setRetryOnNoAnswer] = useState(true);
    const [retryOnVoicemail, setRetryOnVoicemail] = useState(true);
    // Schedule config state
    const [scheduleEnabled, setScheduleEnabled] = useState(false);
    const [scheduleTimezone, setScheduleTimezone] = useState<ITimezoneOption | string>(() => {
        try {
            return Intl.DateTimeFormat().resolvedOptions().timeZone;
        } catch {
            return 'UTC';
        }
    });
    const [timeSlots, setTimeSlots] = useState<TimeSlot[]>([
        { day_of_week: 0, start_time: '09:00', end_time: '17:00' },
    ]);

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

    // Fetch campaign limits
    const fetchCampaignLimits = useCallback(async () => {
        if (!user) return;
        try {
            const accessToken = await getAccessToken();
            const response = await getCampaignLimitsApiV1OrganizationsCampaignLimitsGet({
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.data) {
                setOrgConcurrentLimit(response.data.concurrent_call_limit);
                setFromNumbersCount(response.data.from_numbers_count);
                // Initialize retry config from defaults
                const retryConfig = response.data.default_retry_config;
                setRetryEnabled(retryConfig.enabled);
                setMaxRetries(String(retryConfig.max_retries));
                setRetryDelaySeconds(String(retryConfig.retry_delay_seconds));
                setRetryOnBusy(retryConfig.retry_on_busy);
                setRetryOnNoAnswer(retryConfig.retry_on_no_answer);
                setRetryOnVoicemail(retryConfig.retry_on_voicemail);
            }
        } catch (error) {
            console.error('Failed to fetch campaign limits:', error);
        }
    }, [user, getAccessToken]);

    // Initial load
    useEffect(() => {
        if (user) {
            fetchWorkflows();
            fetchCampaignLimits();
        }
    }, [fetchWorkflows, fetchCampaignLimits, user]);

    // Effective concurrency limit considering both org limit and available CLIs
    const effectiveLimit = fromNumbersCount > 0
        ? Math.min(orgConcurrentLimit, fromNumbersCount)
        : orgConcurrentLimit;

    // Handle form submission
    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setCreateError(null);

        if (!campaignName || !selectedWorkflowId || !sourceId) {
            toast.error('Please fill in all fields');
            return;
        }

        // Validate max_concurrency if provided
        const maxConcurrencyValue = maxConcurrency ? parseInt(maxConcurrency) : null;
        if (maxConcurrencyValue !== null) {
            if (isNaN(maxConcurrencyValue) || maxConcurrencyValue < 1 || maxConcurrencyValue > 100) {
                toast.error('Max concurrent calls must be between 1 and 100');
                return;
            }
            if (maxConcurrencyValue > effectiveLimit) {
                if (fromNumbersCount > 0 && fromNumbersCount < orgConcurrentLimit) {
                    toast.error(`Max concurrent calls cannot exceed ${effectiveLimit}. You have ${fromNumbersCount} phone number(s) configured — add more CLIs to increase concurrency.`);
                } else {
                    toast.error(`Max concurrent calls cannot exceed organization limit (${effectiveLimit})`);
                }
                return;
            }
        }

        setIsSubmitting(true);

        try {
            const accessToken = await getAccessToken();

            const retryConfig = {
                enabled: retryEnabled,
                max_retries: parseInt(maxRetries) || 2,
                retry_delay_seconds: parseInt(retryDelaySeconds) || 120,
                retry_on_busy: retryOnBusy,
                retry_on_no_answer: retryOnNoAnswer,
                retry_on_voicemail: retryOnVoicemail,
            };

            // Build schedule_config if enabled
            const timezoneValue = getTimezoneValue(scheduleTimezone);
            const scheduleConfig = scheduleEnabled && timeSlots.length > 0
                ? {
                    enabled: true,
                    timezone: timezoneValue,
                    slots: timeSlots,
                }
                : undefined;

            const response = await createCampaignApiV1CampaignCreatePost({
                body: {
                    name: campaignName,
                    workflow_id: parseInt(selectedWorkflowId),
                    source_type: sourceType,
                    source_id: sourceId,
                    retry_config: retryConfig,
                    max_concurrency: maxConcurrencyValue,
                    schedule_config: scheduleConfig,
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.error) {
                // Extract error message from API response
                const errorDetail = (response.error as { detail?: string })?.detail;
                const errorMessage = errorDetail || 'Failed to create campaign';
                setCreateError(errorMessage);
                toast.error(errorMessage);
                return;
            }

            if (response.data) {
                toast.success('Campaign created successfully');
                router.push(`/campaigns/${response.data.id}`);
            }
        } catch (error: unknown) {
            console.error('Failed to create campaign:', error);
            const errorMessage = 'Failed to create campaign';
            setCreateError(errorMessage);
            toast.error(errorMessage);
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
        <div className="container mx-auto p-6 pb-12 space-y-6 max-w-2xl">
            <div>
                <Button
                    variant="ghost"
                    onClick={handleBack}
                    className="mb-4"
                >
                    <ArrowLeft className="h-4 w-4 mr-2" />
                    Back to Campaigns
                </Button>
                <h1 className="text-3xl font-bold mb-2">Create New Campaign</h1>
                <p className="text-muted-foreground">Set up a new campaign to execute workflows at scale</p>
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
                                <p className="text-sm text-muted-foreground">
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
                                                    {workflow.name} (#{workflow.id})
                                                </SelectItem>
                                            ))
                                        )}
                                    </SelectContent>
                                </Select>
                                <p className="text-sm text-muted-foreground">
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
                                        {/* <SelectItem value="google-sheet">Google Sheet</SelectItem> */}
                                        <SelectItem value="csv">CSV File</SelectItem>
                                    </SelectContent>
                                </Select>
                                <p className="text-sm text-muted-foreground">
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

                            {/* Advanced Settings */}
                            <Collapsible
                                open={showAdvancedSettings}
                                onOpenChange={setShowAdvancedSettings}
                                className="border rounded-lg"
                            >
                                <CollapsibleTrigger className="flex items-center justify-between w-full p-4 hover:bg-muted/50 transition-colors">
                                    <span className="font-medium">Advanced Settings</span>
                                    {showAdvancedSettings ? (
                                        <ChevronDown className="h-4 w-4" />
                                    ) : (
                                        <ChevronRight className="h-4 w-4" />
                                    )}
                                </CollapsibleTrigger>
                                <CollapsibleContent className="px-4 pb-4">
                                    <CampaignAdvancedSettings
                                        maxConcurrency={maxConcurrency}
                                        onMaxConcurrencyChange={setMaxConcurrency}
                                        effectiveLimit={effectiveLimit}
                                        orgConcurrentLimit={orgConcurrentLimit}
                                        fromNumbersCount={fromNumbersCount}
                                        retryEnabled={retryEnabled}
                                        onRetryEnabledChange={setRetryEnabled}
                                        maxRetries={maxRetries}
                                        onMaxRetriesChange={setMaxRetries}
                                        retryDelaySeconds={retryDelaySeconds}
                                        onRetryDelaySecondsChange={setRetryDelaySeconds}
                                        retryOnBusy={retryOnBusy}
                                        onRetryOnBusyChange={setRetryOnBusy}
                                        retryOnNoAnswer={retryOnNoAnswer}
                                        onRetryOnNoAnswerChange={setRetryOnNoAnswer}
                                        retryOnVoicemail={retryOnVoicemail}
                                        onRetryOnVoicemailChange={setRetryOnVoicemail}
                                        scheduleEnabled={scheduleEnabled}
                                        onScheduleEnabledChange={setScheduleEnabled}
                                        scheduleTimezone={scheduleTimezone}
                                        onScheduleTimezoneChange={setScheduleTimezone}
                                        timeSlots={timeSlots}
                                        onTimeSlotsChange={setTimeSlots}
                                    />
                                </CollapsibleContent>
                            </Collapsible>

                            {createError && (
                                <div className="rounded-md bg-destructive/15 p-3 text-sm text-destructive">
                                    {createError}
                                </div>
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
