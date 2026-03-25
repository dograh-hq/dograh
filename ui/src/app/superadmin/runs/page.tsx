"use client";

import { AlertTriangle, ArrowDown, ArrowUp, ArrowUpDown, CheckCircle, ChevronLeft, ChevronRight, ExternalLink, Info, Loader2, RefreshCw } from 'lucide-react';
import Image from 'next/image';
import { useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useState } from "react";

import { getWorkflowRunsApiV1SuperuserWorkflowRunsGet } from '@/client/sdk.gen';
import { FilterBuilder } from "@/components/filters/FilterBuilder";
import { MediaPreviewButton, MediaPreviewDialog } from '@/components/MediaPreviewDialog';
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAuth } from '@/lib/auth';
import{ superadminFilterAttributes } from "@/lib/filterAttributes";
import { decodeFiltersFromURL, encodeFiltersToURL } from '@/lib/filters';
import { impersonateAsSuperadmin } from '@/lib/utils';
import { ActiveFilter } from '@/types/filters';

interface WorkflowRun {
    id: number;
    name: string;
    workflow_id: number;
    workflow_name?: string;
    user_id?: number;
    organization_id?: number;
    organization_name?: string;
    mode: string;
    is_completed: boolean;
    recording_url?: string;
    transcript_url?: string;
    usage_info?: Record<string, unknown>;
    cost_info?: Record<string, unknown>;
    initial_context?: Record<string, unknown>;
    gathered_context?: Record<string, unknown>;
    created_at: string;
}

interface WorkflowRunsResponse {
    workflow_runs: WorkflowRun[];
    total_count: number;
    page: number;
    limit: number;
    total_pages: number;
}


export default function RunsPage() {
    const router = useRouter();
    const searchParams = useSearchParams();
    const [runs, setRuns] = useState<WorkflowRun[]>([]);
    const [currentPage, setCurrentPage] = useState(() => {
        const pageParam = searchParams.get('page');
        return pageParam ? parseInt(pageParam, 10) : 1;
    });
    const [totalPages, setTotalPages] = useState(1);
    const [totalCount, setTotalCount] = useState(0);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState("");
    const [isExecutingFilters, setIsExecutingFilters] = useState(false);
    const [autoRefresh, setAutoRefresh] = useState(false);
    const [isAutoRefreshing, setIsAutoRefreshing] = useState(false);
    const limit = 50;

    // Initialize filters from URL
    const [activeFilters, setActiveFilters] = useState<ActiveFilter[]>(() => {
        return decodeFiltersFromURL(searchParams, superadminFilterAttributes);
    });

    // Applied filters are the ones actually used for fetching (only updated on Apply click)
    const [appliedFilters, setAppliedFilters] = useState<ActiveFilter[]>(() => {
        return decodeFiltersFromURL(searchParams, superadminFilterAttributes);
    });

    // Sort state (initialized from URL)
    const [sortBy, setSortBy] = useState<string | null>(() => {
        return searchParams.get('sort_by') || null;
    });
    const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>(() => {
        const order = searchParams.get('sort_order');
        return order === 'asc' ? 'asc' : 'desc';
    });

    const [selectedRowId, setSelectedRowId] = useState<number | null>(null);

    const auth = useAuth();

    // Media preview dialog
    const mediaPreview = MediaPreviewDialog();

    const fetchRuns = useCallback(async (
        page: number,
        filters?: ActiveFilter[],
        isAutoRefresh = false,
        sortByParam?: string | null,
        sortOrderParam?: 'asc' | 'desc'
    ) => {
        if (!auth.isAuthenticated) return;

        // Don't show loading state for auto-refresh to prevent UI flicker
        if (!isAutoRefresh) {
            setIsLoading(true);
        } else {
            setIsAutoRefreshing(true);
        }
        setError("");

        try {
            let filterParam = undefined;
            if (filters && filters.length > 0) {
                const filterData = filters.map(filter => ({
                    attribute: filter.attribute.id,
                    type: filter.attribute.type,
                    value: filter.value,
                }));
                filterParam = JSON.stringify(filterData);
            }

            const response = await getWorkflowRunsApiV1SuperuserWorkflowRunsGet({
                query: {
                    page,
                    limit,
                    ...(filterParam && { filters: filterParam }),
                    ...(sortByParam && { sort_by: sortByParam }),
                    ...(sortOrderParam && { sort_order: sortOrderParam }),
                },
            });

            if (response.data) {
                const data = response.data as WorkflowRunsResponse;
                setRuns(data.workflow_runs);
                setCurrentPage(data.page);
                setTotalPages(data.total_pages);
                setTotalCount(data.total_count);
            }
        } catch (err) {
            setError("Failed to fetch workflow runs. Please try again.");
            console.error("Fetch runs error:", err);
        } finally {
            if (!isAutoRefresh) {
                setIsLoading(false);
            } else {
                setIsAutoRefreshing(false);
            }
        }
    }, [limit, auth.isAuthenticated]);

    const updatePageInUrl = useCallback((page: number, filters?: ActiveFilter[], sortByParam?: string | null, sortOrderParam?: 'asc' | 'desc') => {
        const params = new URLSearchParams();
        params.set('page', page.toString());

        // Add filters to URL if present
        if (filters && filters.length > 0) {
            const filterString = encodeFiltersToURL(filters);
            if (filterString) {
                const filterParams = new URLSearchParams(filterString);
                filterParams.forEach((value, key) => params.set(key, value));
            }
        }

        // Add sort to URL if present
        if (sortByParam) {
            params.set('sort_by', sortByParam);
            params.set('sort_order', sortOrderParam || 'desc');
        }

        router.push(`/superadmin/runs?${params.toString()}`);
    }, [router]);

    useEffect(() => {
        // Fetch runs when auth is available and when page/sort changes
        if (auth.isAuthenticated) {
            fetchRuns(currentPage, appliedFilters, false, sortBy, sortOrder);
        }
    }, [currentPage, auth.isAuthenticated, appliedFilters, fetchRuns, sortBy, sortOrder]);

    // Auto-refresh every 5 seconds when enabled and filters are active
    useEffect(() => {
        // Only set up interval if auto-refresh is enabled and there are applied filters
        if (!autoRefresh || appliedFilters.length === 0) {
            return;
        }

        const intervalId = setInterval(() => {
            // Pass true to indicate this is an auto-refresh
            fetchRuns(currentPage, appliedFilters, true, sortBy, sortOrder);
        }, 5000);

        // Cleanup interval on unmount or when dependencies change
        return () => clearInterval(intervalId);
    }, [currentPage, appliedFilters, fetchRuns, autoRefresh, sortBy, sortOrder]);

    const handlePageChange = (page: number) => {
        setCurrentPage(page);
        updatePageInUrl(page, appliedFilters, sortBy, sortOrder);
        fetchRuns(page, appliedFilters, false, sortBy, sortOrder);
    };

    const handleApplyFilters = useCallback(async () => {
        setIsExecutingFilters(true);
        setCurrentPage(1); // Reset to first page when applying filters
        setAppliedFilters(activeFilters); // Update applied filters
        updatePageInUrl(1, activeFilters, sortBy, sortOrder);
        await fetchRuns(1, activeFilters, false, sortBy, sortOrder);
        setIsExecutingFilters(false);
    }, [activeFilters, fetchRuns, updatePageInUrl, sortBy, sortOrder]);

    const handleFiltersChange = useCallback((filters: ActiveFilter[]) => {
        setActiveFilters(filters);
    }, []);

    const handleClearFilters = useCallback(async () => {
        setIsExecutingFilters(true);
        setCurrentPage(1);
        setAppliedFilters([]); // Clear applied filters
        updatePageInUrl(1, [], sortBy, sortOrder); // Clear filters from URL
        await fetchRuns(1, [], false, sortBy, sortOrder); // Fetch all runs without filters
        setIsExecutingFilters(false);
    }, [fetchRuns, updatePageInUrl, sortBy, sortOrder]);

    const handleSort = useCallback((field: string) => {
        // Reset to first page when sort changes
        setCurrentPage(1);

        const newSortBy = field;
        let newSortOrder: 'asc' | 'desc' = 'desc';
        if (sortBy === field) {
            newSortOrder = sortOrder === 'asc' ? 'desc' : 'asc';
        }

        setSortBy(newSortBy);
        setSortOrder(newSortOrder);
        updatePageInUrl(1, appliedFilters, newSortBy, newSortOrder);
    }, [sortBy, sortOrder, updatePageInUrl, appliedFilters]);

    /**
     * ----------------------------------------------------------------------------------
     * Helpers
     * ----------------------------------------------------------------------------------
     */

    const formatDate = (dateString: string) => new Date(dateString).toLocaleString();

    const calculateDuration = (isCompleted: boolean, usageInfo?: Record<string, unknown>) => {
        if (isCompleted && typeof usageInfo?.call_duration_seconds === 'number') {
            return `${Number(usageInfo.call_duration_seconds).toFixed(2)}s`;
        }
        return '-';
    };


    /**
     * Wrapper around shared impersonation util – we only need to fetch the
     * current superadmin token and then delegate the heavy lifting.
     */
    const impersonateAndMaybeRedirect = useCallback(
        async (targetUserId: number | undefined, redirectPath?: string) => {
            if (!targetUserId || !auth.isAuthenticated) return;
            try {
                const token = await auth.getAccessToken();
                await impersonateAsSuperadmin({
                    accessToken: token,
                    userId: targetUserId,
                    redirectPath,
                    openInNewTab: true,
                });
            } catch (err) {
                console.error('Failed to impersonate user', err);
                alert('Failed to impersonate the user. Please try again.');
            }
        },
        [auth],
    );

    if (isLoading && runs.length === 0) {
        return (
            <div className="container mx-auto p-6 flex items-center justify-center min-h-[400px]">
                <div className="flex items-center space-x-2">
                    <Loader2 className="h-6 w-6 animate-spin" />
                    <span>Loading workflow runs...</span>
                </div>
            </div>
        );
    }

    return (
        <div className="container mx-auto p-6 space-y-6 max-w-full">
            <div>
                <h1 className="text-3xl font-bold mb-2">Workflow Runs</h1>
                <p className="text-muted-foreground">View and manage all workflow runs across organizations</p>
            </div>

            {error && (
                    <div className="mb-6 bg-red-50 border border-red-200 text-red-600 px-4 py-3 rounded-lg">
                        {error}
                    </div>
                )}

                <FilterBuilder
                    availableAttributes={superadminFilterAttributes}
                    activeFilters={activeFilters}
                    onFiltersChange={handleFiltersChange}
                    onApplyFilters={handleApplyFilters}
                    onClearFilters={handleClearFilters}
                    isExecuting={isExecutingFilters}
                    autoRefresh={autoRefresh}
                    onAutoRefreshChange={setAutoRefresh}
                    hasAppliedFilters={appliedFilters.length > 0}
                />

                <Card>
                    <CardHeader>
                        <div className="flex items-center justify-between">
                            <div>
                                <CardTitle>All Workflow Runs</CardTitle>
                                <CardDescription>
                                    Showing {runs.length} of {totalCount} total runs
                                </CardDescription>
                            </div>
                            {isAutoRefreshing && (
                                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                    <RefreshCw className="h-4 w-4 animate-spin" />
                                    <span>Refreshing...</span>
                                </div>
                            )}
                        </div>
                    </CardHeader>
                    <CardContent>
                        {runs.length === 0 ? (
                            <div className="text-center py-8 text-muted-foreground">
                                No workflow runs found.
                            </div>
                        ) : (
                            <>
                                <div className="bg-card border rounded-lg overflow-hidden shadow-sm">
                                    <Table>
                                        <TableHeader>
                                            <TableRow className="bg-muted">
                                                <TableHead className="font-semibold">ID</TableHead>
                                                <TableHead className="font-semibold">Workflow</TableHead>
                                                <TableHead className="font-semibold">Status</TableHead>
                                                <TableHead className="font-semibold">Disposition</TableHead>
                                                <TableHead className="font-semibold">Tags</TableHead>
                                                <TableHead
                                                    className="font-semibold cursor-pointer hover:bg-muted/50 select-none"
                                                    onClick={() => handleSort('duration')}
                                                >
                                                    <div className="flex items-center gap-1">
                                                        Duration
                                                        {sortBy === 'duration' ? (
                                                            sortOrder === 'asc' ? <ArrowUp className="h-4 w-4" /> : <ArrowDown className="h-4 w-4" />
                                                        ) : (
                                                            <ArrowUpDown className="h-4 w-4 text-muted-foreground" />
                                                        )}
                                                    </div>
                                                </TableHead>
                                                <TableHead className="font-semibold">Details</TableHead>
                                                <TableHead
                                                    className="font-semibold cursor-pointer hover:bg-muted/50 select-none"
                                                    onClick={() => handleSort('created_at')}
                                                >
                                                    <div className="flex items-center gap-1">
                                                        Created At
                                                        {sortBy === 'created_at' ? (
                                                            sortOrder === 'asc' ? <ArrowUp className="h-4 w-4" /> : <ArrowDown className="h-4 w-4" />
                                                        ) : (
                                                            <ArrowUpDown className="h-4 w-4 text-muted-foreground" />
                                                        )}
                                                    </div>
                                                </TableHead>
                                                <TableHead className="font-semibold">Actions</TableHead>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {runs.map((run) => (
                                                <TableRow
                                                    key={run.id}
                                                    className={selectedRowId === run.id ? "bg-primary/20 ring-1 ring-primary/50" : ""}>
                                                    <TableCell className="font-mono text-sm">
                                                        #{run.id}
                                                    </TableCell>
                                                    <TableCell>
                                                        <div className="flex flex-col">
                                                            <span className="font-medium text-sm">
                                                                {run.workflow_name ? (
                                                                    run.workflow_name.length > 15
                                                                        ? `${run.workflow_name.substring(0, 15)}...`
                                                                        : run.workflow_name
                                                                ) : 'Unknown Workflow'}
                                                            </span>
                                                            <span className="text-xs text-muted-foreground font-mono">
                                                                ID: {String(run.workflow_id).length > 12
                                                                    ? `${String(run.workflow_id).substring(0, 12)}...`
                                                                    : run.workflow_id}
                                                            </span>
                                                        </div>
                                                    </TableCell>
                                                    <TableCell className="text-center">
                                                        {run.is_completed ? (
                                                            <CheckCircle className="h-5 w-5 text-green-600" />
                                                        ) : (
                                                            <AlertTriangle className="h-5 w-5 text-yellow-500" />
                                                        )}
                                                    </TableCell>
                                                    <TableCell>
                                                        {run.gathered_context?.mapped_call_disposition ? (
                                                            <Badge variant="default">
                                                                {run.gathered_context.mapped_call_disposition as string}
                                                            </Badge>
                                                        ) : (
                                                            <span className="text-sm text-muted-foreground">-</span>
                                                        )}
                                                    </TableCell>
                                                    <TableCell>
                                                        {Array.isArray(run.gathered_context?.call_tags) && run.gathered_context.call_tags.length > 0 ? (
                                                            <div className="flex flex-wrap gap-1">
                                                                {run.gathered_context.call_tags.map((tag: string) => (
                                                                    <Badge key={tag} variant="default">
                                                                        {tag}
                                                                    </Badge>
                                                                ))}
                                                            </div>
                                                        ) : (
                                                            <span className="text-sm text-muted-foreground">-</span>
                                                        )}
                                                    </TableCell>
                                                    <TableCell className="text-sm whitespace-pre-wrap break-words">
                                                        <span className={!run.is_completed ? "font-semibold text-blue-600" : ""}>
                                                            {calculateDuration(run.is_completed, run.usage_info)}
                                                        </span>
                                                    </TableCell>
                                                    <TableCell className="text-sm">
                                                        <div className="flex items-center space-x-1">
                                                            {run.gathered_context && (
                                                                <Tooltip>
                                                                    <TooltipTrigger asChild>
                                                                        <Info className="h-4 w-4 text-blue-500 cursor-pointer" />
                                                                    </TooltipTrigger>
                                                                    <TooltipContent sideOffset={4} className="max-w-sm whitespace-pre-wrap break-words">
                                                                        <p className="font-semibold text-xs mb-1">Gathered Context</p>
                                                                        <pre className="max-w-sm whitespace-pre-wrap break-words text-xs">
                                                                            {JSON.stringify(run.gathered_context, null, 2)}
                                                                        </pre>
                                                                    </TooltipContent>
                                                                </Tooltip>
                                                            )}
                                                            {run.usage_info && (
                                                                <Tooltip>
                                                                    <TooltipTrigger asChild>
                                                                        <Info className="h-4 w-4 text-muted-foreground cursor-pointer" />
                                                                    </TooltipTrigger>
                                                                    <TooltipContent sideOffset={4} className="max-w-sm whitespace-pre-wrap break-words">
                                                                        <p className="font-semibold text-xs mb-1">Usage Info</p>
                                                                        <pre className="max-w-sm whitespace-pre-wrap break-words text-xs">
                                                                            {JSON.stringify(run.usage_info, null, 2)}
                                                                        </pre>
                                                                    </TooltipContent>
                                                                </Tooltip>
                                                            )}
                                                            {!run.gathered_context && !run.usage_info && (
                                                                <span className="text-muted-foreground">-</span>
                                                            )}
                                                        </div>
                                                    </TableCell>
                                                    <TableCell className="text-sm">
                                                        {formatDate(run.created_at)}
                                                    </TableCell>
                                                    <TableCell>
                                                        <div className="flex space-x-2">
                                                            <MediaPreviewButton
                                                                recordingUrl={run.recording_url}
                                                                transcriptUrl={run.transcript_url}
                                                                runId={run.id}
                                                                onOpenPreview={mediaPreview.openPreview}
                                                                onSelect={setSelectedRowId}
                                                            />
                                                            <Button
                                                                variant="outline"
                                                                size="icon"
                                                                onClick={() => {
                                                                    const query = encodeURIComponent(
                                                                        JSON.stringify({
                                                                            children: [
                                                                                {
                                                                                    field: 'extra.run_id',
                                                                                    op: '==',
                                                                                    value: run.id,
                                                                                },
                                                                            ],
                                                                            field: '',
                                                                            op: 'and',
                                                                        }),
                                                                    );
                                                                    window.open(
                                                                        `https://app.axiom.co/dograh-of6c/stream/${process.env.NEXT_PUBLIC_AXIOM_LOG_DATASET}?q=${query}`,
                                                                        '_blank',
                                                                    );
                                                                }}
                                                            >
                                                                <Image
                                                                    src="/axiom_icon.svg"
                                                                    alt="Traces"
                                                                    width={16}
                                                                    height={16}
                                                                    className="h-4 w-4"
                                                                />
                                                            </Button>

                                                            <Button
                                                                variant="outline"
                                                                size="icon"
                                                                onClick={() => {
                                                                    if (run.gathered_context?.trace_url) {
                                                                        window.open(String(run.gathered_context.trace_url), '_blank');
                                                                    } else {
                                                                        const filter = encodeURIComponent(
                                                                            `metadata;stringObject;attributes;contains;conversation.id,metadata;stringObject;attributes;contains;${run.id}`,
                                                                        );
                                                                        window.open(
                                                                            `${process.env.NEXT_PUBLIC_LANGFUSE_ENDPOINT}/project/${process.env.NEXT_PUBLIC_LANGFUSE_PROJECT_ID}/traces?search=&filter=${filter}&dateRange=All+time`,
                                                                            '_blank',
                                                                        );
                                                                    }
                                                                }}
                                                            >
                                                                <Image
                                                                    src="/langfuse_icon.svg"
                                                                    alt="Langfuse Traces"
                                                                    width={16}
                                                                    height={16}
                                                                    className="h-4 w-4"
                                                                />
                                                            </Button>

                                                            {/* Quick-link to open the workflow inside the *regular* app after
                                                                successfully impersonating the owner of the workflow. */}
                                                            <Button
                                                                variant="outline"
                                                                size="icon"
                                                                title="Open workflow as user"
                                                                onClick={() => {
                                                                    const appBaseUrl = window.location.origin.includes('superadmin.')
                                                                        ? window.location.origin.replace('superadmin.', 'app.')
                                                                        : window.location.origin;
                                                                    impersonateAndMaybeRedirect(
                                                                        run.user_id,
                                                                        `${appBaseUrl}/workflow/${run.workflow_id}`,
                                                                    );
                                                                }}
                                                            >
                                                                <ExternalLink className="h-4 w-4" />
                                                            </Button>

                                                        </div>
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>
                                </div>

                                {/* Pagination */}
                                {totalPages > 1 && (
                                    <div className="flex items-center justify-between mt-6">
                                        <div className="text-sm text-muted-foreground">
                                            Page {currentPage} of {totalPages} ({totalCount} total runs)
                                        </div>
                                        <div className="flex space-x-2">
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                onClick={() => handlePageChange(currentPage - 1)}
                                                disabled={currentPage === 1 || isLoading}
                                            >
                                                <ChevronLeft className="h-4 w-4 mr-1" />
                                                Previous
                                            </Button>

                                            {/* Page numbers */}
                                            {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                                                let pageNum;
                                                if (totalPages <= 5) {
                                                    pageNum = i + 1;
                                                } else if (currentPage <= 3) {
                                                    pageNum = i + 1;
                                                } else if (currentPage >= totalPages - 2) {
                                                    pageNum = totalPages - 4 + i;
                                                } else {
                                                    pageNum = currentPage - 2 + i;
                                                }

                                                return (
                                                    <Button
                                                        key={pageNum}
                                                        variant={currentPage === pageNum ? "default" : "outline"}
                                                        size="sm"
                                                        onClick={() => handlePageChange(pageNum)}
                                                        disabled={isLoading}
                                                    >
                                                        {pageNum}
                                                    </Button>
                                                );
                                            })}

                                            <Button
                                                variant="outline"
                                                size="sm"
                                                onClick={() => handlePageChange(currentPage + 1)}
                                                disabled={currentPage === totalPages || isLoading}
                                            >
                                                Next
                                                <ChevronRight className="h-4 w-4 ml-1" />
                                            </Button>
                                        </div>
                                    </div>
                                )}
                            </>
                        )}
                    </CardContent>
                </Card>

                {/* Media Preview Dialog */}
                {mediaPreview.dialog}

        </div>
    );
}
