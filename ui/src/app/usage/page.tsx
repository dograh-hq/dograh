"use client";

import { Calendar, ChevronLeft, ChevronRight, Globe } from 'lucide-react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useId, useState } from 'react';
import TimezoneSelect, { type ITimezoneOption } from 'react-timezone-select';

import { getCurrentPeriodUsageApiV1OrganizationsUsageCurrentPeriodGet, getDailyUsageBreakdownApiV1OrganizationsUsageDailyBreakdownGet,getUsageHistoryApiV1OrganizationsUsageRunsGet } from '@/client/sdk.gen';
import type { CurrentUsageResponse, DailyUsageBreakdownResponse,UsageHistoryResponse, WorkflowRunUsageResponse } from '@/client/types.gen';
import { DailyUsageTable } from '@/components/DailyUsageTable';
import { FilterBuilder } from '@/components/filters/FilterBuilder';
import { MediaPreviewButtons, MediaPreviewDialog } from '@/components/MediaPreviewDialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table';
import { useUserConfig } from '@/context/UserConfigContext';
import { getDispositionBadgeVariant } from '@/lib/dispositionBadgeVariant';
import { usageFilterAttributes } from '@/lib/filterAttributes';
import { decodeFiltersFromURL, encodeFiltersToURL } from '@/lib/filters';
import { ActiveFilter, DateRangeValue } from '@/types/filters';

// Get local timezone
const getLocalTimezone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;

export default function UsagePage() {
    const router = useRouter();
    const searchParams = useSearchParams();
    const { userConfig, saveUserConfig, loading: userConfigLoading, accessToken, organizationPricing } = useUserConfig();

    // Current usage state
    const [currentUsage, setCurrentUsage] = useState<CurrentUsageResponse | null>(null);
    const [isLoadingCurrent, setIsLoadingCurrent] = useState(true);

    // Usage history state
    const [usageHistory, setUsageHistory] = useState<UsageHistoryResponse | null>(null);
    const [isLoadingHistory, setIsLoadingHistory] = useState(false);
    const [currentPage, setCurrentPage] = useState(() => {
        const pageParam = searchParams.get('page');
        return pageParam ? parseInt(pageParam, 10) : 1;
    });
    const [isExecutingFilters, setIsExecutingFilters] = useState(false);

    // Daily usage breakdown state (only for paid orgs)
    const [dailyUsage, setDailyUsage] = useState<DailyUsageBreakdownResponse | null>(null);
    const [isLoadingDaily, setIsLoadingDaily] = useState(false);

    // Initialize filters from URL
    const [activeFilters, setActiveFilters] = useState<ActiveFilter[]>(() => {
        return decodeFiltersFromURL(searchParams, usageFilterAttributes);
    });

    // Media preview dialog
    const mediaPreview = MediaPreviewDialog({ accessToken });

    // Timezone state - initialize with empty string to avoid hydration mismatch
    const localTimezone = getLocalTimezone();
    const [selectedTimezone, setSelectedTimezone] = useState<ITimezoneOption | string>('');
    const [savingTimezone, setSavingTimezone] = useState(false);
    const timezoneSelectId = useId(); // Stable ID for react-select to prevent hydration mismatch

    // Fetch current usage
    const fetchCurrentUsage = useCallback(async () => {
        if (!accessToken) return;
        try {
            const response = await getCurrentPeriodUsageApiV1OrganizationsUsageCurrentPeriodGet({
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.data) {
                setCurrentUsage(response.data);
            }
        } catch (error) {
            console.error('Failed to fetch current usage:', error);
        } finally {
            setIsLoadingCurrent(false);
        }
    }, [accessToken]);

    // Fetch usage history
    const fetchUsageHistory = useCallback(async (page: number, filters?: ActiveFilter[]) => {
        if (!accessToken) return;
        setIsLoadingHistory(true);
        try {
            let filterParam = undefined;
            let startDate = '';
            let endDate = '';

            if (filters && filters.length > 0) {
                // Extract date range filter if present
                const dateRangeFilter = filters.find(f => f.attribute.id === 'dateRange');
                if (dateRangeFilter && dateRangeFilter.value) {
                    const dateValue = dateRangeFilter.value as DateRangeValue;

                    if (dateValue.from) {
                        // The dates are already in the user's local timezone
                        // Convert to UTC ISO string for the backend
                        startDate = dateValue.from.toISOString();
                    }
                    if (dateValue.to) {
                        // Convert to UTC ISO string for the backend
                        endDate = dateValue.to.toISOString();
                    }
                }

                // Process other filters (excluding dateRange)
                const otherFilters = filters.filter(f => f.attribute.id !== 'dateRange');
                if (otherFilters.length > 0) {
                    const filterData = otherFilters.map(filter => ({
                        attribute: filter.attribute.id,
                        type: filter.attribute.type,
                        value: filter.value,
                    }));
                    filterParam = JSON.stringify(filterData);
                }
            }

            const response = await getUsageHistoryApiV1OrganizationsUsageRunsGet({
                query: {
                    page,
                    limit: 50,
                    ...(startDate && { start_date: startDate }),
                    ...(endDate && { end_date: endDate }),
                    ...(filterParam && { filters: filterParam })
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.data) {
                setUsageHistory(response.data);
            }
        } catch (error) {
            console.error('Failed to fetch usage history:', error);
        } finally {
            setIsLoadingHistory(false);
        }
    }, [accessToken]);

    // Fetch daily usage breakdown
    const fetchDailyUsage = useCallback(async () => {
        if (!accessToken || !organizationPricing?.price_per_second_usd) return;

        setIsLoadingDaily(true);
        try {
            const response = await getDailyUsageBreakdownApiV1OrganizationsUsageDailyBreakdownGet({
                query: { days: 7 },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.data) {
                setDailyUsage(response.data);
            }
        } catch (error) {
            console.error('Failed to fetch daily usage:', error);
        } finally {
            setIsLoadingDaily(false);
        }
    }, [accessToken, organizationPricing]);

    // Handle timezone change
    const handleTimezoneChange = async (timezone: ITimezoneOption | string) => {
        setSelectedTimezone(timezone);
        setSavingTimezone(true);
        try {
            const tzValue = typeof timezone === 'string' ? timezone : timezone.value;
            await saveUserConfig({ timezone: tzValue });
        } catch (error) {
            console.error('Failed to save timezone:', error);
            // Revert to previous timezone on error
            const prevTz = userConfig?.timezone || localTimezone;
            setSelectedTimezone(prevTz);
        } finally {
            setSavingTimezone(false);
        }
    };

    // Update timezone when userConfig loads
    useEffect(() => {
        if (!userConfigLoading) {
            // Config has loaded - set the timezone
            if (userConfig?.timezone) {
                setSelectedTimezone(userConfig.timezone);
            } else {
                // No saved timezone, use local
                setSelectedTimezone(localTimezone);
            }
        }
    }, [userConfig, userConfigLoading, localTimezone]);

    // Initial load - fetch when accessToken becomes available
    useEffect(() => {
        if (accessToken) {
            fetchCurrentUsage();
            fetchUsageHistory(currentPage, activeFilters);
        }
    }, [accessToken, currentPage, activeFilters, fetchUsageHistory, fetchCurrentUsage]);

    // Fetch daily usage when organizationPricing becomes available
    useEffect(() => {
        if (accessToken && organizationPricing?.price_per_second_usd) {
            fetchDailyUsage();
        }
    }, [accessToken, organizationPricing, fetchDailyUsage]);

    // Update URL with query parameters
    const updateUrlParams = useCallback((params: { page?: number; filters?: ActiveFilter[] }) => {
        const newParams = new URLSearchParams();

        if (params.page !== undefined) {
            newParams.set('page', params.page.toString());
        }

        // Add filters to URL if present
        if (params.filters && params.filters.length > 0) {
            const filterString = encodeFiltersToURL(params.filters);
            if (filterString) {
                const filterParams = new URLSearchParams(filterString);
                filterParams.forEach((value, key) => newParams.set(key, value));
            }
        }

        router.push(`/usage?${newParams.toString()}`);
    }, [router]);

    const handleApplyFilters = useCallback(async () => {
        setIsExecutingFilters(true);
        setCurrentPage(1); // Reset to first page when applying filters
        updateUrlParams({ page: 1, filters: activeFilters });
        await fetchUsageHistory(1, activeFilters);
        setIsExecutingFilters(false);
    }, [activeFilters, fetchUsageHistory, updateUrlParams]);

    const handleFiltersChange = useCallback((filters: ActiveFilter[]) => {
        setActiveFilters(filters);
    }, []);

    const handleClearFilters = useCallback(async () => {
        setIsExecutingFilters(true);
        setCurrentPage(1);
        updateUrlParams({ page: 1, filters: [] }); // Clear filters from URL
        await fetchUsageHistory(1, []); // Fetch all runs without filters
        setIsExecutingFilters(false);
    }, [fetchUsageHistory, updateUrlParams]);

    // Handle page change
    const handlePageChange = (newPage: number) => {
        setCurrentPage(newPage);
        updateUrlParams({ page: newPage, filters: activeFilters });
        fetchUsageHistory(newPage, activeFilters);
    };

    // Handle row click to navigate to workflow run
    const handleRowClick = (run: WorkflowRunUsageResponse) => {
        router.push(`/workflow/${run.workflow_id}/run/${run.id}`);
    };

    // Format date for display with timezone support
    const formatDate = (dateString: string) => {
        const date = new Date(dateString);
        const tzValue = typeof selectedTimezone === 'string' ? selectedTimezone : selectedTimezone.value;
        // Use local timezone if none selected (during loading)
        const effectiveTz = tzValue || localTimezone;
        return date.toLocaleDateString('en-US', {
            timeZone: effectiveTz,
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        });
    };

    // Format datetime for display with timezone support
    const formatDateTime = (dateString: string) => {
        const date = new Date(dateString);
        const tzValue = typeof selectedTimezone === 'string' ? selectedTimezone : selectedTimezone.value;
        // Use local timezone if none selected (during loading)
        const effectiveTz = tzValue || localTimezone;
        return date.toLocaleString('en-US', {
            timeZone: effectiveTz,
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            hour12: true
        });
    };

    // Format duration for display
    const formatDuration = (seconds: number) => {
        const minutes = Math.floor(seconds / 60);
        const remainingSeconds = seconds % 60;
        if (minutes === 0) return `${remainingSeconds}s`;
        if (remainingSeconds === 0) return `${minutes}m`;
        return `${minutes}m ${remainingSeconds}s`;
    };

    return (
        <div className="container mx-auto p-6 space-y-6">
            <div>
                <div className="flex justify-between items-start">
                    <div>
                        <h1 className="text-3xl font-bold text-gray-900 mb-2">Usage Dashboard</h1>
                        <p className="text-gray-600">Monitor your Dograh Token usage and quota</p>
                    </div>
                        <div className="flex items-center gap-2">
                            <Globe className="h-4 w-4 text-gray-500" />
                            <div className="w-[300px]">
                                <TimezoneSelect
                                    instanceId={timezoneSelectId}
                                    value={selectedTimezone}
                                    onChange={handleTimezoneChange}
                                    isDisabled={savingTimezone || userConfigLoading}
                                    placeholder={userConfigLoading ? "Loading..." : "Select timezone"}
                                    styles={{
                                        control: (base) => ({
                                            ...base,
                                            minHeight: '36px',
                                            fontSize: '14px',
                                        }),
                                        menu: (base) => ({
                                            ...base,
                                            zIndex: 9999,
                                        }),
                                    }}
                                />
                            </div>
                        </div>
                    </div>
                </div>

                {/* Current Period Card */}
                <Card className="mb-6">
                    <CardHeader>
                        <CardTitle>Current Billing Period</CardTitle>
                        <CardDescription>
                            {currentUsage && `${formatDate(currentUsage.period_start)} - ${formatDate(currentUsage.period_end)}`}
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        {isLoadingCurrent ? (
                            <div className="animate-pulse space-y-4">
                                <div className="h-4 bg-gray-200 rounded w-1/4"></div>
                                <div className="h-8 bg-gray-200 rounded"></div>
                                <div className="h-4 bg-gray-200 rounded w-1/3"></div>
                            </div>
                        ) : currentUsage ? (
                            <div className="space-y-4">
                                <div className="flex justify-between items-baseline">
                                    <div>
                                        {organizationPricing?.price_per_second_usd ? (
                                            <>
                                                <p className="text-2xl font-bold">
                                                    ${(currentUsage.used_amount_usd || 0).toFixed(2)}
                                                </p>
                                                <p className="text-sm text-gray-600">Total Cost (USD)</p>
                                                <p className="text-xs text-gray-500 mt-1">
                                                    Rate: ${(organizationPricing.price_per_second_usd * 60).toFixed(4)}/minute
                                                </p>
                                            </>
                                        ) : (
                                            <>
                                                <p className="text-2xl font-bold">
                                                    {currentUsage.used_dograh_tokens.toLocaleString()} / {currentUsage.quota_dograh_tokens.toLocaleString()}
                                                </p>
                                                <p className="text-sm text-gray-600">Dograh Tokens</p>
                                            </>
                                        )}
                                    </div>
                                    {!organizationPricing?.price_per_second_usd && (
                                        <div className="text-right">
                                            <p className="text-lg font-semibold">{currentUsage.percentage_used}%</p>
                                            <p className="text-sm text-gray-600">Used</p>
                                        </div>
                                    )}
                                </div>

                                {!organizationPricing?.price_per_second_usd && (
                                    <Progress value={currentUsage.percentage_used} className="h-3" />
                                )}

                                <div className="flex justify-between items-center text-sm text-gray-600">
                                    <div className="flex items-center">
                                        <Calendar className="h-4 w-4 mr-1" />
                                        Next refresh: {formatDate(currentUsage.next_refresh_date)}
                                    </div>
                                    <div>
                                        Total Duration: <span className="font-medium text-gray-900">{formatDuration(currentUsage.total_duration_seconds)}</span>
                                    </div>
                                </div>
                            </div>
                        ) : (
                            <p className="text-gray-500">Unable to load usage data</p>
                        )}
                    </CardContent>
                </Card>

                {/* Daily Usage Table - Only for paid organizations */}
                {organizationPricing?.price_per_second_usd && (
                    <div className="mb-6">
                        <DailyUsageTable
                            data={dailyUsage}
                            isLoading={isLoadingDaily}
                        />
                    </div>
                )}

                {/* Filter Builder */}
                <div className="mb-6">
                    <FilterBuilder
                        availableAttributes={usageFilterAttributes}
                        activeFilters={activeFilters}
                        onFiltersChange={handleFiltersChange}
                        onApplyFilters={handleApplyFilters}
                        onClearFilters={handleClearFilters}
                        isExecuting={isExecutingFilters}
                    />
                </div>

                {/* Usage History */}
                <Card>
                    <CardHeader>
                        <div className="flex justify-between items-start">
                            <div className="space-y-1.5">
                                <CardTitle>Usage History</CardTitle>
                                <CardDescription>
                                    View detailed usage by workflow run
                                </CardDescription>
                            </div>
                        </div>
                    </CardHeader>
                    <CardContent>
                        {isLoadingHistory ? (
                            <div className="animate-pulse space-y-3">
                                {[...Array(5)].map((_, i) => (
                                    <div key={i} className="h-12 bg-gray-200 rounded"></div>
                                ))}
                            </div>
                        ) : usageHistory && usageHistory.runs.length > 0 ? (
                            <>
                                <div className="bg-white border rounded-lg overflow-hidden shadow-sm">
                                    <Table>
                                        <TableHeader>
                                            <TableRow className="bg-gray-50">
                                                <TableHead className="font-semibold">Run ID</TableHead>
                                                <TableHead className="font-semibold">Workflow Name</TableHead>
                                                <TableHead className="font-semibold">Phone Number</TableHead>
                                                <TableHead className="font-semibold">Disposition</TableHead>
                                                <TableHead className="font-semibold">Date</TableHead>
                                                <TableHead className="font-semibold text-right">Duration</TableHead>
                                                <TableHead className="font-semibold text-right">
                                                    {organizationPricing?.price_per_second_usd ? 'Cost (USD)' : 'Dograh Tokens'}
                                                </TableHead>
                                                <TableHead className="font-semibold">Actions</TableHead>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {usageHistory.runs.map((run) => (
                                                <TableRow
                                                    key={run.id}
                                                >
                                                    <TableCell
                                                        className="font-mono text-sm cursor-pointer hover:underline"
                                                        onClick={() => handleRowClick(run)}
                                                    >
                                                        #{run.id}
                                                    </TableCell>
                                                    <TableCell>{run.workflow_name || 'Unknown'}</TableCell>
                                                    <TableCell className="text-sm">
                                                        {run.phone_number || '-'}
                                                    </TableCell>
                                                    <TableCell>
                                                        {run.disposition ? (
                                                            <Badge variant={getDispositionBadgeVariant(run.disposition)}>
                                                                {run.disposition}
                                                            </Badge>
                                                        ) : (
                                                            <span className="text-sm text-muted-foreground">-</span>
                                                        )}
                                                    </TableCell>
                                                    <TableCell>{formatDateTime(run.created_at)}</TableCell>
                                                    <TableCell className="text-right">
                                                        {formatDuration(run.call_duration_seconds)}
                                                    </TableCell>
                                                    <TableCell className="text-right font-medium">
                                                        {organizationPricing?.price_per_second_usd && run.charge_usd !== undefined && run.charge_usd !== null
                                                            ? `$${run.charge_usd.toFixed(2)}`
                                                            : run.dograh_token_usage.toLocaleString()
                                                        }
                                                    </TableCell>
                                                    <TableCell>
                                                        <MediaPreviewButtons
                                                            recordingUrl={run.recording_url}
                                                            transcriptUrl={run.transcript_url}
                                                            runId={run.id}
                                                            onOpenAudio={mediaPreview.openAudioModal}
                                                            onOpenTranscript={mediaPreview.openTranscriptModal}
                                                        />
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>
                                </div>

                                {/* Summary */}
                                {activeFilters.length > 0 && (
                                    <div className="mt-4 p-3 bg-gray-50 rounded-md">
                                        <p className="text-sm text-gray-600">
                                            Total for filtered period: <span className="font-semibold text-gray-900">
                                                {usageHistory.total_dograh_tokens.toLocaleString()} Dograh Tokens
                                            </span>
                                            {' • '}
                                            <span className="font-semibold text-gray-900">
                                                {formatDuration(usageHistory.total_duration_seconds)}
                                            </span>
                                        </p>
                                    </div>
                                )}

                                {/* Pagination */}
                                {usageHistory.total_pages > 1 && (
                                    <div className="flex items-center justify-between mt-6">
                                        <p className="text-sm text-gray-600">
                                            Page {usageHistory.page} of {usageHistory.total_pages} ({usageHistory.total_count} total runs)
                                        </p>
                                        <div className="flex gap-2">
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                onClick={() => handlePageChange(currentPage - 1)}
                                                disabled={currentPage === 1}
                                            >
                                                <ChevronLeft className="h-4 w-4" />
                                                Previous
                                            </Button>
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                onClick={() => handlePageChange(currentPage + 1)}
                                                disabled={currentPage === usageHistory.total_pages}
                                            >
                                                Next
                                                <ChevronRight className="h-4 w-4" />
                                            </Button>
                                        </div>
                                    </div>
                                )}
                            </>
                        ) : (
                            <p className="text-center py-8 text-gray-500">No usage history found</p>
                        )}
                    </CardContent>
                </Card>

                {/* Media Preview Dialog */}
                {mediaPreview.dialog}
        </div>
    );
}

