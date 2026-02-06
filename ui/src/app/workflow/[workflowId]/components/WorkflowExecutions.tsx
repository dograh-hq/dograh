"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { getWorkflowApiV1WorkflowFetchWorkflowIdGet, getWorkflowRunsApiV1WorkflowWorkflowIdRunsGet } from "@/client/sdk.gen";
import { WorkflowRunResponseSchema } from "@/client/types.gen";
import { WorkflowRunsTable } from "@/components/workflow-runs";
import { DISPOSITION_CODES } from "@/constants/dispositionCodes";
import { useUserConfig } from '@/context/UserConfigContext';
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

    // Sort state (initialized from URL)
    const [sortBy, setSortBy] = useState<string | null>(() => {
        return searchParams.get('sort_by') || null;
    });
    const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>(() => {
        const order = searchParams.get('sort_order');
        return order === 'asc' ? 'asc' : 'desc';
    });

    const { accessToken } = useUserConfig();

    // Initialize filters from URL
    const [activeFilters, setActiveFilters] = useState<ActiveFilter[]>(() => {
        return decodeFiltersFromURL(searchParams, availableAttributes);
    });

    // Applied filters are the ones actually used for fetching (only updated on Apply click)
    const [appliedFilters, setAppliedFilters] = useState<ActiveFilter[]>(() => {
        return decodeFiltersFromURL(searchParams, availableAttributes);
    });

    // Load disposition codes from workflow configuration
    const loadDispositionCodes = useCallback(async () => {
        if (!accessToken) return;
        try {
            const response = await getWorkflowApiV1WorkflowFetchWorkflowIdGet({
                path: { workflow_id: Number(workflowId) },
                headers: { 'Authorization': `Bearer ${accessToken}` }
            });

            const workflow = response.data;
            if (workflow?.call_disposition_codes) {
                // Update the disposition code attribute with actual options
                setConfiguredAttributes(prev => prev.map(attr => {
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
                }));
            }
        } catch (err) {
            console.error("Failed to load disposition codes:", err);
        }
    }, [workflowId, accessToken]);

    useEffect(() => {
        loadDispositionCodes();
    }, [loadDispositionCodes]);

    const fetchWorkflowRuns = useCallback(async (
        page: number,
        filters?: ActiveFilter[],
        sortByParam?: string | null,
        sortOrderParam?: 'asc' | 'desc'
    ) => {
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
                    ...(filterParam && { filters: filterParam }),
                    ...(sortByParam && { sort_by: sortByParam }),
                    ...(sortOrderParam && { sort_order: sortOrderParam }),
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

        router.push(`/workflow/${workflowId}/runs?${params.toString()}`, { scroll: false });
    }, [router, workflowId]);

    useEffect(() => {
        fetchWorkflowRuns(currentPage, appliedFilters, sortBy, sortOrder);
    }, [currentPage, appliedFilters, fetchWorkflowRuns, sortBy, sortOrder]);

    const handleApplyFilters = useCallback(async () => {
        setIsExecutingFilters(true);
        setCurrentPage(1); // Reset to first page when applying filters
        setAppliedFilters(activeFilters);
        updatePageInUrl(1, activeFilters, sortBy, sortOrder);
        await fetchWorkflowRuns(1, activeFilters, sortBy, sortOrder);
        setIsExecutingFilters(false);
    }, [activeFilters, fetchWorkflowRuns, updatePageInUrl, sortBy, sortOrder]);

    const handleFiltersChange = useCallback((filters: ActiveFilter[]) => {
        setActiveFilters(filters);
    }, []);

    const handleClearFilters = useCallback(async () => {
        setIsExecutingFilters(true);
        setCurrentPage(1);
        setActiveFilters([]);
        setAppliedFilters([]);
        updatePageInUrl(1, [], sortBy, sortOrder); // Clear filters from URL
        await fetchWorkflowRuns(1, [], sortBy, sortOrder); // Fetch all workflows without filters
        setIsExecutingFilters(false);
    }, [fetchWorkflowRuns, updatePageInUrl, sortBy, sortOrder]);

    const handlePageChange = useCallback((page: number) => {
        setCurrentPage(page);
        updatePageInUrl(page, appliedFilters, sortBy, sortOrder);
    }, [updatePageInUrl, appliedFilters, sortBy, sortOrder]);

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

    const handleReload = useCallback(() => {
        fetchWorkflowRuns(currentPage, appliedFilters, sortBy, sortOrder);
    }, [fetchWorkflowRuns, currentPage, appliedFilters, sortBy, sortOrder]);

    return (
        <div className="container mx-auto py-8">
            <WorkflowRunsTable
                runs={workflowRuns}
                loading={loading}
                error={error}
                currentPage={currentPage}
                totalPages={totalPages}
                totalCount={totalCount}
                onPageChange={handlePageChange}
                availableAttributes={configuredAttributes}
                activeFilters={activeFilters}
                onFiltersChange={handleFiltersChange}
                onApplyFilters={handleApplyFilters}
                onClearFilters={handleClearFilters}
                isExecutingFilters={isExecutingFilters}
                hasAppliedFilters={appliedFilters.length > 0}
                sortBy={sortBy}
                sortOrder={sortOrder}
                onSort={handleSort}
                workflowId={workflowId}
                accessToken={accessToken}
                onReload={handleReload}
            />
        </div>
    );
}
