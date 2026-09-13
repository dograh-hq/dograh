"use client";

import { Clock } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { getCampaignRunsApiV1CampaignCampaignIdRunsGet } from "@/client/sdk.gen";
import { WorkflowRunResponseSchema } from "@/client/types.gen";
import { WorkflowRunsTable } from "@/components/workflow-runs";
import { useDispositionCodes } from "@/hooks/useDispositionCodes";
import { useAuth } from "@/lib/auth";
import { withDispositionCodeOptions } from "@/lib/filterAttributes";
import { decodeFiltersFromURL, encodeFiltersToURL } from "@/lib/filters";
import { ActiveFilter, availableAttributes } from "@/types/filters";

interface CampaignRunsProps {
    campaignId: number;
    workflowId: number;
    searchParams?: URLSearchParams;
    refreshTrigger?: number;
    requiresCallPermission?: boolean;
    onSyncWhatsAppPermissions?: () => Promise<void>;
}

export function CampaignRuns({
    campaignId,
    workflowId,
    searchParams,
    refreshTrigger,
    requiresCallPermission,
    onSyncWhatsAppPermissions,
}: CampaignRunsProps) {
    const router = useRouter();
    const { isAuthenticated } = useAuth();
    const { codes: dispositionCodes } = useDispositionCodes();
    const campaignFilterAttributes = useMemo(
        () => withDispositionCodeOptions(
            availableAttributes.filter(attribute => (
                ['dateRange', 'dispositionCode', 'duration', 'status', 'tokenUsage'].includes(attribute.id)
            )),
            dispositionCodes
        ),
        [dispositionCodes]
    );
    const [runs, setRuns] = useState<WorkflowRunResponseSchema[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [currentPage, setCurrentPage] = useState(() => {
        const pageParam = searchParams?.get('page');
        return pageParam ? parseInt(pageParam, 10) : 1;
    });
    const [totalPages, setTotalPages] = useState(1);
    const [totalCount, setTotalCount] = useState(0);
    const [isExecutingFilters, setIsExecutingFilters] = useState(false);

    // Sort state (initialized from URL)
    const [sortBy, setSortBy] = useState<string | null>(() => {
        return searchParams?.get('sort_by') || null;
    });
    const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>(() => {
        const order = searchParams?.get('sort_order');
        return order === 'asc' ? 'asc' : 'desc';
    });

    // Initialize filters from URL
    const [activeFilters, setActiveFilters] = useState<ActiveFilter[]>(() => {
        return searchParams ? decodeFiltersFromURL(searchParams, availableAttributes) : [];
    });

    // Applied filters are the ones actually used for fetching (only updated on Apply click)
    const [appliedFilters, setAppliedFilters] = useState<ActiveFilter[]>(() => {
        return searchParams ? decodeFiltersFromURL(searchParams, availableAttributes) : [];
    });

    const fetchCampaignRuns = useCallback(async (
        page: number,
        filters?: ActiveFilter[],
        sortByParam?: string | null,
        sortOrderParam?: 'asc' | 'desc'
    ) => {
        if (!isAuthenticated) return;
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

            const response = await getCampaignRunsApiV1CampaignCampaignIdRunsGet({
                path: { campaign_id: campaignId },
                query: {
                    page: page,
                    limit: 50,
                    ...(filterParam && { filters: filterParam }),
                    ...(sortByParam && { sort_by: sortByParam }),
                    ...(sortOrderParam && { sort_order: sortOrderParam }),
                },
            });

            if (response.error) {
                throw new Error("Failed to fetch campaign runs");
            }

            if (response.data) {
                // The API returns runs as array of dicts, convert to WorkflowRunResponseSchema
                setRuns((response.data.runs || []) as unknown as WorkflowRunResponseSchema[]);
                setTotalPages(response.data.total_pages || 1);
                setTotalCount(response.data.total_count || 0);
                setCurrentPage(response.data.page || 1);
            }
            setError(null);
        } catch (err) {
            console.error("Error fetching campaign runs:", err);
            setError("Failed to load campaign runs");
        } finally {
            setLoading(false);
        }
    }, [campaignId, isAuthenticated]);

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

        router.push(`/campaigns/${campaignId}?${params.toString()}`, { scroll: false });
    }, [router, campaignId]);

    useEffect(() => {
        if (isAuthenticated) {
            fetchCampaignRuns(currentPage, appliedFilters, sortBy, sortOrder);
        }
    }, [currentPage, appliedFilters, fetchCampaignRuns, isAuthenticated, sortBy, sortOrder, refreshTrigger]);

    const hasAwaitingPermission = useMemo(() => {
        return runs.some(
            (run) =>
                run.gathered_context?.mapped_call_disposition === "awaiting_permission" ||
                // The dispatcher writes both keys into gathered_context when it
                // parks a run; the raw one was being read off the run itself,
                // where it does not exist, so this clause never matched.
                run.gathered_context?.call_disposition === "awaiting_permission"
        );
    }, [runs]);

    const handleApplyFilters = useCallback(async () => {
        setIsExecutingFilters(true);
        setCurrentPage(1);
        setAppliedFilters(activeFilters);
        updatePageInUrl(1, activeFilters, sortBy, sortOrder);
        await fetchCampaignRuns(1, activeFilters, sortBy, sortOrder);
        setIsExecutingFilters(false);
    }, [activeFilters, fetchCampaignRuns, updatePageInUrl, sortBy, sortOrder]);

    const handleFiltersChange = useCallback((filters: ActiveFilter[]) => {
        setActiveFilters(filters);
    }, []);

    const handleClearFilters = useCallback(async () => {
        setIsExecutingFilters(true);
        setCurrentPage(1);
        setActiveFilters([]);
        setAppliedFilters([]);
        updatePageInUrl(1, [], sortBy, sortOrder);
        await fetchCampaignRuns(1, [], sortBy, sortOrder);
        setIsExecutingFilters(false);
    }, [fetchCampaignRuns, updatePageInUrl, sortBy, sortOrder]);

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

    const handleReload = useCallback(async () => {
        if (requiresCallPermission && onSyncWhatsAppPermissions) {
            await onSyncWhatsAppPermissions();
        } else {
            await fetchCampaignRuns(currentPage, appliedFilters, sortBy, sortOrder);
        }
    }, [fetchCampaignRuns, currentPage, appliedFilters, sortBy, sortOrder, requiresCallPermission, onSyncWhatsAppPermissions]);

    return (
        <div className="space-y-4">
            {hasAwaitingPermission && (
                <div className="flex items-center gap-3 p-4 rounded-lg border border-amber-300 bg-amber-50/70 text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
                    <Clock className="h-5 w-5 text-amber-600 dark:text-amber-400 shrink-0" />
                    <div className="text-sm">
                        <span className="font-semibold">Leads awaiting WhatsApp call permission: </span>
                        <span>
                            Dograh sent permission request templates to these contacts. Calls will trigger automatically as soon as contacts tap &ldquo;Allow&rdquo; in WhatsApp. You can also click the reload button on the runs table to recheck status with Meta immediately.
                        </span>
                    </div>
                </div>
            )}
            <WorkflowRunsTable
                runs={runs}
                loading={loading}
                error={error}
                currentPage={currentPage}
                totalPages={totalPages}
                totalCount={totalCount}
                onPageChange={handlePageChange}
                availableAttributes={campaignFilterAttributes}
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
                onReload={handleReload}
                title="Campaign Workflow Runs"
                emptyMessage="No workflow runs found for this campaign"
            />
        </div>
    );
}
