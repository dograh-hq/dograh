"use client";

import { ChevronLeft, ChevronRight, Download, ExternalLink } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { getWorkflowApiV1WorkflowFetchWorkflowIdGet, getWorkflowRunsApiV1WorkflowWorkflowIdRunsGet } from "@/client/sdk.gen";
import { WorkflowRunResponseSchema } from "@/client/types.gen";
import { FilterBuilder } from "@/components/filters/FilterBuilder";
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
import { DISPOSITION_CODES } from "@/constants/dispositionCodes";
import { useUserConfig } from '@/context/UserConfigContext';
import { getDispositionBadgeVariant } from '@/lib/dispositionBadgeVariant';
import { downloadFile } from "@/lib/files";
import { decodeFiltersFromURL, encodeFiltersToURL } from "@/lib/filters";
import { ActiveFilter, availableAttributes, FilterAttribute } from "@/types/filters";

interface WorkflowExecutionsProps {
    workflowId: number;
    searchParams: URLSearchParams;
}

export function WorkflowExecutions({ workflowId, searchParams }: WorkflowExecutionsProps) {
    const router = useRouter();
    const [workflowRuns, setWorkflowRuns] = useState<WorkflowRunResponseSchema[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [currentPage, setCurrentPage] = useState(() => {
        const pageParam = searchParams.get('page');
        return pageParam ? parseInt(pageParam, 10) : 1;
    });
    const [totalPages, setTotalPages] = useState(1);
    const [totalCount, setTotalCount] = useState(0);
    const [isExecutingFilters, setIsExecutingFilters] = useState(false);
    const [configuredAttributes, setConfiguredAttributes] = useState<FilterAttribute[]>(availableAttributes);

    const { accessToken } = useUserConfig();

    // Initialize filters from URL
    const [activeFilters, setActiveFilters] = useState<ActiveFilter[]>(() => {
        return decodeFiltersFromURL(searchParams, availableAttributes);
    });

    const formatDate = (dateString: string) => new Date(dateString).toLocaleString();

    // Load disposition codes from workflow configuration
    useEffect(() => {
        const loadDispositionCodes = async () => {
            if (!accessToken) return;
            try {
                const response = await getWorkflowApiV1WorkflowFetchWorkflowIdGet({
                    path: { workflow_id: Number(workflowId) },
                    headers: { 'Authorization': `Bearer ${accessToken}` }
                });

                const workflow = response.data;
                if (workflow?.call_disposition_codes) {
                    // Update the disposition code attribute with actual options
                    const updatedAttributes = configuredAttributes.map(attr => {
                        if (attr.id === 'dispositionCode') {
                            return {
                                ...attr,
                                config: {
                                    ...attr.config,
                                    options: Object.keys(workflow.call_disposition_codes || {}).length > 0
                                                        ? Object.keys(workflow.call_disposition_codes || {})
                                                        : [...DISPOSITION_CODES]
                                }
                            };
                        }
                        return attr;
                    });
                    setConfiguredAttributes(updatedAttributes);
                }
            } catch (err) {
                console.error("Failed to load disposition codes:", err);
            }
        };

        loadDispositionCodes();
    }, [workflowId, accessToken]);

    const fetchWorkflowRuns = useCallback(async (page: number, filters?: ActiveFilter[]) => {
        if (!accessToken) return;
        try {
            setLoading(true);
            // Prepare filter data for API
            let filterParam = undefined;
            if (filters && filters.length > 0) {
                const filterData = filters.map(filter => ({
                    attribute: filter.attribute.id,
                    type: filter.attribute.type,
                    value: filter.value
                }));
                filterParam = JSON.stringify(filterData);
            }

            const response = await getWorkflowRunsApiV1WorkflowWorkflowIdRunsGet({
                path: { workflow_id: Number(workflowId) },
                query: {
                    page: page,
                    limit: 50,
                    ...(filterParam && { filters: filterParam })
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.error) {
                throw new Error("Failed to fetch workflow runs");
            }

            if (response.data) {
                setWorkflowRuns(response.data.runs || []);
                setTotalPages(response.data.total_pages || 1);
                setTotalCount(response.data.total_count || 0);
                setCurrentPage(response.data.page || 1);
            }
            setError(null);
        } catch (err) {
            console.error("Error fetching workflow runs:", err);
            setError("Failed to load workflow runs");
        } finally {
            setLoading(false);
        }
    }, [workflowId, accessToken]);

    const updatePageInUrl = useCallback((page: number, filters?: ActiveFilter[]) => {
        const params = new URLSearchParams();
        params.set('tab', 'executions');
        params.set('page', page.toString());

        // Add filters to URL if present
        if (filters && filters.length > 0) {
            const filterString = encodeFiltersToURL(filters);
            if (filterString) {
                const filterParams = new URLSearchParams(filterString);
                filterParams.forEach((value, key) => params.set(key, value));
            }
        }

        router.push(`/workflow/${workflowId}?${params.toString()}`, { scroll: false });
    }, [router, workflowId]);

    useEffect(() => {
        fetchWorkflowRuns(currentPage, activeFilters);
    }, [currentPage, activeFilters, fetchWorkflowRuns]);

    const handleApplyFilters = useCallback(async () => {
        setIsExecutingFilters(true);
        setCurrentPage(1); // Reset to first page when applying filters
        updatePageInUrl(1, activeFilters);
        await fetchWorkflowRuns(1, activeFilters);
        setIsExecutingFilters(false);
    }, [activeFilters, fetchWorkflowRuns, updatePageInUrl]);

    const handleFiltersChange = useCallback((filters: ActiveFilter[]) => {
        setActiveFilters(filters);
    }, []);

    const handleClearFilters = useCallback(async () => {
        setIsExecutingFilters(true);
        setCurrentPage(1);
        updatePageInUrl(1, []); // Clear filters from URL
        await fetchWorkflowRuns(1, []); // Fetch all workflows without filters
        setIsExecutingFilters(false);
    }, [fetchWorkflowRuns, updatePageInUrl]);

    return (
        <div className="container mx-auto py-8">
            <div className="mb-6">
                <h1 className="text-2xl font-bold mb-4">Workflow Run History</h1>
                <FilterBuilder
                    availableAttributes={configuredAttributes}
                    activeFilters={activeFilters}
                    onFiltersChange={handleFiltersChange}
                    onApplyFilters={handleApplyFilters}
                    onClearFilters={handleClearFilters}
                    isExecuting={isExecutingFilters}
                />
            </div>
            {loading ? (
                <div className="flex justify-center">
                    <div className="animate-pulse">Loading workflow runs...</div>
                </div>
            ) : error ? (
                <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded">
                    {error}
                </div>
            ) : workflowRuns.length === 0 ? (
                <div className="text-center py-8">
                    <p className="text-gray-500">No workflow runs found</p>
                </div>
            ) : (
                <Card>
                    <CardHeader>
                        <CardTitle>Workflow Runs</CardTitle>
                        <CardDescription>
                            Showing {workflowRuns.length} of {totalCount} total runs
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        <div className="bg-white border rounded-lg overflow-hidden shadow-sm">
                            <Table>
                                <TableHeader>
                                    <TableRow className="bg-gray-50">
                                        <TableHead className="font-semibold">ID</TableHead>
                                        <TableHead className="font-semibold">Status</TableHead>
                                        <TableHead className="font-semibold">Created At</TableHead>
                                        <TableHead className="font-semibold">Duration</TableHead>
                                        <TableHead className="font-semibold">Disposition</TableHead>
                                        <TableHead className="font-semibold">Dograh Token</TableHead>
                                        <TableHead className="font-semibold">Actions</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {workflowRuns.map((run) => (
                                        <TableRow
                                            key={run.id}
                                            className="cursor-pointer"
                                            onClick={() => window.open(`/workflow/${workflowId}/run/${run.id}`, '_blank')}
                                        >
                                            <TableCell className="font-mono text-sm">#{run.id}</TableCell>
                                            <TableCell>
                                                <Badge variant={run.is_completed ? "default" : "secondary"}>
                                                    {run.is_completed ? "Completed" : "In Progress"}
                                                </Badge>
                                            </TableCell>
                                            <TableCell className="text-sm">{formatDate(run.created_at)}</TableCell>
                                            <TableCell className="text-sm">
                                                {typeof run.cost_info?.call_duration_seconds === 'number'
                                                    ? `${run.cost_info.call_duration_seconds.toFixed(1)}s`
                                                    : "-"}
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
                                            <TableCell className="text-sm">
                                                {typeof run.cost_info?.dograh_token_usage === 'number'
                                                    ? `${run.cost_info.dograh_token_usage.toFixed(2)}`
                                                    : "-"}
                                            </TableCell>
                                            <TableCell>
                                                <div className="flex space-x-2">
                                                    {run.transcript_url && (
                                                        <Button
                                                            variant="outline"
                                                            size="sm"
                                                            onClick={(e) => {
                                                                e.stopPropagation();
                                                                if (accessToken) downloadFile(run.transcript_url, accessToken);
                                                            }}
                                                        >
                                                            <Download className="h-3 w-3 mr-1" />
                                                            Transcript
                                                        </Button>
                                                    )}
                                                    {run.recording_url && (
                                                        <Button
                                                            variant="outline"
                                                            size="sm"
                                                            onClick={(e) => {
                                                                e.stopPropagation();
                                                                if (accessToken) downloadFile(run.recording_url, accessToken);
                                                            }}
                                                        >
                                                            <Download className="h-3 w-3 mr-1" />
                                                            Recording
                                                        </Button>
                                                    )}
                                                    <Button
                                                        variant="outline"
                                                        size="sm"
                                                        onClick={(e) => {
                                                            e.stopPropagation();
                                                            window.open(`/workflow/${workflowId}/run/${run.id}`, '_blank');
                                                        }}
                                                    >
                                                        <ExternalLink className="h-3 w-3 mr-1" />
                                                        View
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
                                <p className="text-sm text-gray-600">
                                    Page {currentPage} of {totalPages}
                                </p>
                                <div className="flex gap-2">
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => {
                                            const newPage = currentPage - 1;
                                            setCurrentPage(newPage);
                                            updatePageInUrl(newPage, activeFilters);
                                        }}
                                        disabled={currentPage === 1}
                                    >
                                        <ChevronLeft className="h-4 w-4" />
                                        Previous
                                    </Button>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => {
                                            const newPage = currentPage + 1;
                                            setCurrentPage(newPage);
                                            updatePageInUrl(newPage, activeFilters);
                                        }}
                                        disabled={currentPage === totalPages}
                                    >
                                        Next
                                        <ChevronRight className="h-4 w-4" />
                                    </Button>
                                </div>
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}
        </div>
    );
}
