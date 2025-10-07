"use client";

import { AlertTriangle, CheckCircle, ChevronLeft, ChevronRight, ExternalLink, Info, Loader2, MessageSquare, RefreshCw } from 'lucide-react';
import Image from 'next/image';
import { useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useState } from "react";

import { getWorkflowRunsApiV1SuperuserWorkflowRunsGet, setAdminCommentApiV1SuperuserWorkflowRunsRunIdCommentPost } from '@/client/sdk.gen';
import { FilterBuilder } from "@/components/filters/FilterBuilder";
import { MediaPreviewButtons, MediaPreviewDialog } from '@/components/MediaPreviewDialog';
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
    Dialog,
    DialogClose,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { Textarea } from '@/components/ui/textarea';
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useUserConfig } from '@/context/UserConfigContext';
import { getDispositionBadgeVariant } from '@/lib/dispositionBadgeVariant';
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
    admin_comment?: string;
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
    const [currentTime, setCurrentTime] = useState(Date.now());
    const limit = 50;

    // Initialize filters from URL
    const [activeFilters, setActiveFilters] = useState<ActiveFilter[]>(() => {
        return decodeFiltersFromURL(searchParams, superadminFilterAttributes);
    });

    // Dialog state for comment editing
    const [isCommentDialogOpen, setIsCommentDialogOpen] = useState(false);
    const [commentRunId, setCommentRunId] = useState<number | null>(null);
    const [commentText, setCommentText] = useState('');
    const [selectedRowId, setSelectedRowId] = useState<number | null>(null);

    const { accessToken } = useUserConfig();

    // Media preview dialog
    const mediaPreview = MediaPreviewDialog({ accessToken });

    const fetchRuns = useCallback(async (page: number, filters?: ActiveFilter[], isAutoRefresh = false) => {
        if (!accessToken) return;

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
                    ...(filterParam && { filters: filterParam })
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
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
    }, [limit, accessToken]);

    const updatePageInUrl = useCallback((page: number, filters?: ActiveFilter[]) => {
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

        router.push(`/superadmin/runs?${params.toString()}`);
    }, [router]);

    useEffect(() => {
        // Fetch runs when token is available and when page changes
        if (accessToken) {
            fetchRuns(currentPage, activeFilters);
        }
    }, [currentPage, accessToken, activeFilters, fetchRuns]);

    // Auto-refresh every 5 seconds when enabled and filters are active
    useEffect(() => {
        // Only set up interval if auto-refresh is enabled and there are active filters
        if (!autoRefresh || activeFilters.length === 0) {
            return;
        }

        const intervalId = setInterval(() => {
            // Pass true to indicate this is an auto-refresh
            fetchRuns(currentPage, activeFilters, true);
        }, 5000);

        // Cleanup interval on unmount or when dependencies change
        return () => clearInterval(intervalId);
    }, [currentPage, activeFilters, fetchRuns, autoRefresh]);

    // Update current time every second to show live duration for running calls
    useEffect(() => {
        const hasRunningCalls = runs.some(run => !run.is_completed);
        if (!hasRunningCalls) {
            return;
        }

        const intervalId = setInterval(() => {
            setCurrentTime(Date.now());
        }, 1000);

        return () => clearInterval(intervalId);
    }, [runs]);

    const handlePageChange = (page: number) => {
        setCurrentPage(page);
        updatePageInUrl(page, activeFilters);
        fetchRuns(page, activeFilters);
    };

    const handleApplyFilters = useCallback(async () => {
        setIsExecutingFilters(true);
        setCurrentPage(1); // Reset to first page when applying filters
        updatePageInUrl(1, activeFilters);
        await fetchRuns(1, activeFilters);
        setIsExecutingFilters(false);
    }, [activeFilters, fetchRuns, updatePageInUrl]);

    const handleFiltersChange = useCallback((filters: ActiveFilter[]) => {
        setActiveFilters(filters);
    }, []);

    const handleClearFilters = useCallback(async () => {
        setIsExecutingFilters(true);
        setCurrentPage(1);
        updatePageInUrl(1, []); // Clear filters from URL
        await fetchRuns(1, []); // Fetch all runs without filters
        setIsExecutingFilters(false);
    }, [fetchRuns, updatePageInUrl]);

    // Save comment function declared outside JSX (requirement #2)
    const saveAdminComment = useCallback(async () => {
        if (commentRunId === null || !accessToken) return;
        try {
            await setAdminCommentApiV1SuperuserWorkflowRunsRunIdCommentPost({
                path: {
                    run_id: commentRunId,
                },
                body: {
                    admin_comment: commentText,
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
            });

            // Optimistically update UI
            setRuns(prev => prev.map(r => r.id === commentRunId ? { ...r, admin_comment: commentText } : r));

            setIsCommentDialogOpen(false);
        } catch (err) {
            console.error('Failed to set admin comment', err);
            alert('Failed to save comment. Please try again.');
        }
    }, [commentRunId, commentText, accessToken]);

    /**
     * ----------------------------------------------------------------------------------
     * Helpers
     * ----------------------------------------------------------------------------------
     */

    const formatDate = (dateString: string) => new Date(dateString).toLocaleString();

    const calculateDuration = (createdAt: string, isCompleted: boolean, usageInfo?: Record<string, unknown>) => {
        if (isCompleted && typeof usageInfo?.call_duration_seconds === 'number') {
            return `${Number(usageInfo.call_duration_seconds).toFixed(2)}s`;
        }

        if (!isCompleted) {
            const startTime = new Date(createdAt).getTime();
            const duration = Math.floor((currentTime - startTime) / 1000);

            // If duration exceeds 5 minutes (300 seconds), show "-" as it's likely an error
            if (duration > 300) {
                return '-';
            }

            if (duration < 60) {
                return `${duration}s`;
            } else {
                const minutes = Math.floor(duration / 60);
                const seconds = duration % 60;
                return `${minutes}m ${seconds}s`;
            }
        }

        return '-';
    };


    /**
     * Wrapper around shared impersonation util – we only need to fetch the
     * current superadmin token and then delegate the heavy lifting.
     */
    const impersonateAndMaybeRedirect = useCallback(
        async (targetUserId: number | undefined, redirectPath?: string) => {
            if (!targetUserId || !accessToken) return;
            try {
                await impersonateAsSuperadmin({
                    accessToken: accessToken,
                    userId: targetUserId,
                    redirectPath,
                    openInNewTab: true,
                });
            } catch (err) {
                console.error('Failed to impersonate user', err);
                alert('Failed to impersonate the user. Please try again.');
            }
        },
        [accessToken],
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
                <h1 className="text-3xl font-bold text-gray-900 mb-2">Workflow Runs</h1>
                <p className="text-gray-600">View and manage all workflow runs across organizations</p>
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
                            <div className="text-center py-8 text-gray-500">
                                No workflow runs found.
                            </div>
                        ) : (
                            <>
                                <div className="bg-white border rounded-lg overflow-hidden shadow-sm">
                                    <Table>
                                        <TableHeader>
                                            <TableRow className="bg-gray-50">
                                                <TableHead className="font-semibold">ID</TableHead>
                                                <TableHead className="font-semibold">Workflow</TableHead>
                                                <TableHead className="font-semibold">Status</TableHead>
                                                <TableHead className="font-semibold">Disposition</TableHead>
                                                <TableHead className="font-semibold">Tags</TableHead>
                                                <TableHead className="font-semibold">Comment</TableHead>
                                                <TableHead className="font-semibold">Duration</TableHead>
                                                <TableHead className="font-semibold">Dograh Token</TableHead>
                                                <TableHead className="font-semibold">Created At</TableHead>
                                                <TableHead className="font-semibold">Actions</TableHead>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {runs.map((run) => (
                                                <TableRow
                                                    key={run.id}
                                                    className={selectedRowId === run.id ? "bg-blue-50" : ""}>
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
                                                            <span className="text-xs text-gray-500 font-mono">
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
                                                            <Badge variant={getDispositionBadgeVariant(run.gathered_context.mapped_call_disposition as string)}>
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
                                                    <TableCell className="max-w-sm whitespace-pre-wrap break-words">
                                                        {run.admin_comment ? (
                                                            <span>{run.admin_comment}</span>
                                                        ) : (
                                                            <span className="text-gray-400 italic">No comment</span>
                                                        )}
                                                    </TableCell>
                                                    <TableCell className="text-sm whitespace-pre-wrap break-words">
                                                        <span className={!run.is_completed ? "font-semibold text-blue-600" : ""}>
                                                            {calculateDuration(run.created_at, run.is_completed, run.usage_info)}
                                                        </span>
                                                    </TableCell>
                                                    <TableCell className="text-sm">
                                                        <div className="flex items-center space-x-1">
                                                            <span>
                                                                {typeof run.cost_info?.total_cost_usd === 'number'
                                                                    ? `${Number(run.cost_info.total_cost_usd * 100).toFixed(2)}`
                                                                    : '-'}
                                                            </span>
                                                            {(run.usage_info || run.cost_info) && (
                                                                <Tooltip>
                                                                    <TooltipTrigger asChild>
                                                                        <Info className="h-4 w-4 text-gray-500 cursor-pointer" />
                                                                    </TooltipTrigger>
                                                                    <TooltipContent sideOffset={4} className="max-w-xs whitespace-pre-wrap break-words">
                                                                        <pre className="max-w-xs whitespace-pre-wrap break-words">
                                                                            {`Usage Info: ${JSON.stringify(run.usage_info ?? {}, null, 2)}\n\nCost Info: ${JSON.stringify(run.cost_info ?? {}, null, 2)}`}
                                                                        </pre>
                                                                    </TooltipContent>
                                                                </Tooltip>
                                                            )}
                                                        </div>
                                                    </TableCell>
                                                    <TableCell className="text-sm">
                                                        {formatDate(run.created_at)}
                                                    </TableCell>
                                                    <TableCell>
                                                        <div className="flex space-x-2">
                                                            <MediaPreviewButtons
                                                                recordingUrl={run.recording_url}
                                                                transcriptUrl={run.transcript_url}
                                                                runId={run.id}
                                                                onOpenAudio={mediaPreview.openAudioModal}
                                                                onOpenTranscript={mediaPreview.openTranscriptModal}
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
                                                                    const filter = encodeURIComponent(
                                                                        `metadata;stringObject;attributes;contains;conversation.id,metadata;stringObject;attributes;contains;${run.id}`,
                                                                    );
                                                                    window.open(
                                                                        `${process.env.NEXT_PUBLIC_LANGFUSE_ENDPOINT}/project/${process.env.NEXT_PUBLIC_LANGFUSE_PROJECT_ID}/traces?search=&filter=${filter}&dateRange=All+time`,
                                                                        '_blank',
                                                                    );
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

                                                            <Button
                                                                variant="outline"
                                                                size="icon"
                                                                onClick={() => {
                                                                    setCommentRunId(run.id);
                                                                    setCommentText(run.admin_comment || '');
                                                                    setIsCommentDialogOpen(true);
                                                                }}
                                                                title="Add/Edit Comment"
                                                            >
                                                                <MessageSquare className="h-4 w-4" />
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
                                        <div className="text-sm text-gray-500">
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

                {/* Comment Dialog */}
                <Dialog open={isCommentDialogOpen} onOpenChange={setIsCommentDialogOpen}>
                    <DialogContent className="sm:max-w-lg">
                        <DialogHeader>
                            <DialogTitle>{commentRunId ? 'Edit Comment' : 'Add Comment'}</DialogTitle>
                            <DialogDescription>
                                Admin-only comment for run #{commentRunId}
                            </DialogDescription>
                        </DialogHeader>

                        <Textarea
                            value={commentText}
                            onChange={(e) => setCommentText(e.target.value)}
                            placeholder="Enter comment here..."
                            className="min-h-[120px]"
                        />

                        <DialogFooter className="pt-4">
                            <DialogClose asChild>
                                <Button variant="secondary">Cancel</Button>
                            </DialogClose>
                            <Button onClick={saveAdminComment}>Save</Button>
                        </DialogFooter>
                    </DialogContent>
                </Dialog>

                {/* Media Preview Dialog */}
                {mediaPreview.dialog}

        </div>
    );
}
