"use client";

import {
    ChevronLeft,
    ChevronRight,
    CircleDollarSign,
    CreditCard,
    ExternalLink,
    Info,
    RefreshCw,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { createMpsCreditPurchaseUrlApiV1OrganizationsUsageMpsCreditsPurchaseUrlPost, getBillingCreditsApiV1OrganizationsBillingCreditsGet } from "@/client/sdk.gen";
import type { MpsBillingCreditsResponse, MpsCreditLedgerEntryResponse } from "@/client/types.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { useAppConfig } from "@/context/AppConfigContext";
import { useOrganizationTimezone } from "@/hooks/useOrganizationTimezone";
import { useAuth } from "@/lib/auth";
import { formatDateTime } from "@/lib/dateTime";

const LEDGER_PAGE_SIZE = 50;

const formatCredits = (value: number | null | undefined) => (
    (value ?? 0).toLocaleString(undefined, {
        maximumFractionDigits: 2,
        minimumFractionDigits: 0,
    })
);

const formatAmount = (amountMinor?: number | null, currency?: string | null) => {
    if (amountMinor == null) {
        return "-";
    }

    return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: currency || "USD",
    }).format(amountMinor / 100);
};

const metricLabels: Record<string, string> = {
    voice_minutes: "Voice usage",
    platform_usage: "Platform usage",
};

const formatTitleCase = (value: string | null | undefined) => (
    value ? value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()) : "-"
);

const getLedgerEntryLabel = (entry: MpsCreditLedgerEntryResponse) => {
    if (entry.metric_code) {
        return metricLabels[entry.metric_code] ?? formatTitleCase(entry.metric_code);
    }

    if (entry.entry_type === "grant") {
        return "Credit grant";
    }

    if (entry.entry_type === "purchase") {
        return "Credit purchase";
    }

    return formatTitleCase(entry.entry_type);
};

const formatBillableQuantity = (entry: MpsCreditLedgerEntryResponse) => {
    if (entry.billable_quantity == null || !entry.quantity_unit) {
        return null;
    }

    const unit = entry.quantity_unit === "minute" ? "min" : entry.quantity_unit;
    return `${formatCredits(entry.billable_quantity)} ${unit}`;
};

const getRunHref = (entry: MpsCreditLedgerEntryResponse) => {
    if (!entry.workflow_id || !entry.workflow_run_id) {
        return null;
    }

    return `/workflow/${entry.workflow_id}/run/${entry.workflow_run_id}`;
};

const getPageFromSearchParams = (
    searchParams: { get: (name: string) => string | null },
) => {
    const pageParam = searchParams.get("page");
    const page = pageParam ? Number.parseInt(pageParam, 10) : 1;
    return Number.isFinite(page) && page > 0 ? page : 1;
};

export default function BillingPage() {
    const router = useRouter();
    const searchParams = useSearchParams();
    const auth = useAuth();
    const { config, loading: configLoading } = useAppConfig();
    const organizationTimezone = useOrganizationTimezone();
    const [credits, setCredits] = useState<MpsBillingCreditsResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [purchasing, setPurchasing] = useState(false);
    const [currentPage, setCurrentPage] = useState(
        () => getPageFromSearchParams(searchParams),
    );

    const hasAppConfig = !configLoading && config !== null;
    const isOssMode = hasAppConfig && config.deploymentMode === "oss";
    const canPurchaseCredits = hasAppConfig && config.deploymentMode !== "oss";
    const totalQuota = credits?.total_quota ?? 0;
    const remainingCredits = credits?.remaining_credits ?? 0;
    const usedCredits = credits?.total_credits_used ?? 0;
    const usagePercent = totalQuota > 0 ? Math.min(100, Math.round((usedCredits / totalQuota) * 100)) : 0;

    const ledgerEntries = useMemo(() => credits?.ledger_entries ?? [], [credits?.ledger_entries]);
    const ledgerPage = credits?.page ?? currentPage;
    const ledgerTotalCount = credits?.total_count ?? ledgerEntries.length;
    const ledgerTotalPages = credits?.total_pages ?? 0;

    const fetchCredits = useCallback(async (
        page: number,
        { silent = false }: { silent?: boolean } = {},
    ) => {
        if (auth.loading) {
            return;
        }

        if (!auth.isAuthenticated) {
            setLoading(false);
            return;
        }

        if (silent) {
            setRefreshing(true);
        } else {
            setLoading(true);
        }

        try {
            const response = await getBillingCreditsApiV1OrganizationsBillingCreditsGet({
                query: { page, limit: LEDGER_PAGE_SIZE },
            });

            if (response.error) {
                throw new Error("Failed to fetch billing credits");
            }

            setCredits(response.data ?? null);
        } catch (error) {
            console.error("Failed to fetch billing credits:", error);
            toast.error("Failed to fetch billing credits");
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, [auth.isAuthenticated, auth.loading]);

    useEffect(() => {
        const nextPage = getPageFromSearchParams(searchParams);
        setCurrentPage((previousPage) => (
            previousPage === nextPage ? previousPage : nextPage
        ));
    }, [searchParams]);

    useEffect(() => {
        fetchCredits(currentPage);
    }, [currentPage, fetchCredits]);

    const handleRefresh = () => {
        fetchCredits(currentPage, { silent: true });
    };

    const updateUrlPage = useCallback((page: number) => {
        const newParams = new URLSearchParams(searchParams.toString());
        if (page > 1) {
            newParams.set("page", page.toString());
        } else {
            newParams.delete("page");
        }

        const queryString = newParams.toString();
        router.push(queryString ? `/billing?${queryString}` : "/billing");
    }, [router, searchParams]);

    const handlePageChange = (page: number) => {
        const nextPage = Math.max(1, page);
        setCurrentPage(nextPage);
        updateUrlPage(nextPage);
    };

    const handlePurchaseCredits = async () => {
        if (!canPurchaseCredits) {
            return;
        }

        setPurchasing(true);
        try {
            const response = await createMpsCreditPurchaseUrlApiV1OrganizationsUsageMpsCreditsPurchaseUrlPost();
            const checkoutUrl = response.data?.checkout_url;
            if (!checkoutUrl) {
                throw new Error("Missing checkout URL");
            }
            window.location.href = checkoutUrl;
        } catch (error) {
            console.error("Failed to create credit purchase URL:", error);
            toast.error("Failed to open checkout");
            setPurchasing(false);
        }
    };

    if (loading || configLoading) {
        return (
            <div className="container mx-auto p-6 space-y-6">
                <div className="space-y-2">
                    <Skeleton className="h-9 w-40" />
                    <Skeleton className="h-5 w-96 max-w-full" />
                </div>
                <div className="grid gap-4 md:grid-cols-2">
                    <Skeleton className="h-36 rounded-lg" />
                    <Skeleton className="h-36 rounded-lg" />
                </div>
                <Skeleton className="h-80 rounded-lg" />
            </div>
        );
    }

    return (
        <div className="container mx-auto p-6 space-y-6">
            <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                <div>
                    <h1 className="text-3xl font-bold mb-2">Billing</h1>
                    <p className="text-muted-foreground">
                        Credits, balance, and account usage for your organization.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Button variant="outline" onClick={handleRefresh} disabled={refreshing}>
                        <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? "animate-spin" : ""}`} />
                        Refresh
                    </Button>
                    {canPurchaseCredits && (
                        <Button onClick={handlePurchaseCredits} disabled={purchasing}>
                            <CreditCard className="h-4 w-4 mr-2" />
                            {purchasing ? "Opening..." : "Add Credits"}
                        </Button>
                    )}
                </div>
            </div>

            {isOssMode && (
                <div className="flex gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4 dark:border-amber-900/50 dark:bg-amber-950/30">
                    <Info className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-600 dark:text-amber-400" />
                    <div className="text-sm text-amber-900 dark:text-amber-200">
                        <p className="font-medium">Credit purchases are unavailable in OSS mode</p>
                        <p className="mt-1">
                            You can&apos;t purchase credits from this self-hosted app. Sign up and
                            purchase credits at{" "}
                            <a
                                href="https://app.dograh.com"
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-1 font-medium underline underline-offset-2"
                            >
                                app.dograh.com
                                <ExternalLink className="h-3 w-3" />
                            </a>
                            . Then add the generated service key in{" "}
                            <Link
                                href="/model-configurations"
                                className="font-medium underline underline-offset-2"
                            >
                                Model Configurations
                            </Link>
                            . Usage for that service key is visible in app.dograh.com.
                        </p>
                    </div>
                </div>
            )}

            <div className="grid gap-4 md:grid-cols-2">
                <Card>
                    <CardHeader className="pb-2">
                        <CardDescription>{isOssMode ? "Credits remaining" : "Credit balance"}</CardDescription>
                        <CardTitle className="flex items-center gap-2 text-3xl">
                            <CircleDollarSign className="h-6 w-6 text-muted-foreground" />
                            {formatCredits(remainingCredits)}
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-sm text-muted-foreground">1 credit = 1 cent</p>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader className="pb-2">
                        <CardDescription>Credits used</CardDescription>
                        <CardTitle className="text-3xl">{formatCredits(usedCredits)}</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-sm text-muted-foreground">
                            {isOssMode ? "Current allocation usage" : "Total ledger debits"}
                        </p>
                    </CardContent>
                </Card>
            </div>

            {!isOssMode ? (
                <Card>
                    <CardHeader>
                        <CardTitle>Credit Ledger</CardTitle>
                        <CardDescription>Recent grants, purchases, and usage debits.</CardDescription>
                    </CardHeader>
                    <CardContent>
                        {ledgerEntries.length > 0 ? (
                            <div className="bg-card border rounded-lg overflow-x-auto shadow-sm">
                                <Table>
                                    <TableHeader>
                                        <TableRow className="bg-muted/50">
                                            <TableHead>Date</TableHead>
                                            <TableHead>Activity</TableHead>
                                            <TableHead>Origin</TableHead>
                                            <TableHead>Run</TableHead>
                                            <TableHead className="text-right">Delta</TableHead>
                                            <TableHead className="text-right">Balance</TableHead>
                                            <TableHead className="text-right">Amount</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {ledgerEntries.map((entry) => {
                                            const delta = entry.credits_delta ?? 0;
                                            const runHref = getRunHref(entry);
                                            const billableQuantity = formatBillableQuantity(entry);
                                            return (
                                                <TableRow key={entry.id}>
                                                    <TableCell>
                                                        {formatDateTime(entry.created_at, organizationTimezone)}
                                                    </TableCell>
                                                    <TableCell>
                                                        <div className="flex flex-col gap-1">
                                                            <span className="font-medium">{getLedgerEntryLabel(entry)}</span>
                                                            {billableQuantity && (
                                                                <span className="text-xs text-muted-foreground">{billableQuantity}</span>
                                                            )}
                                                        </div>
                                                    </TableCell>
                                                    <TableCell>
                                                        {entry.origin ? (
                                                            <Badge variant="secondary">{formatTitleCase(entry.origin)}</Badge>
                                                        ) : (
                                                            "-"
                                                        )}
                                                    </TableCell>
                                                    <TableCell>
                                                        {entry.workflow_run_id ? (
                                                            runHref ? (
                                                                <Link className="font-medium text-primary hover:underline" href={runHref}>
                                                                    #{entry.workflow_run_id}
                                                                </Link>
                                                            ) : (
                                                                <span>#{entry.workflow_run_id}</span>
                                                            )
                                                        ) : (
                                                            "-"
                                                        )}
                                                    </TableCell>
                                                    <TableCell className={`text-right font-medium ${delta >= 0 ? "text-green-600" : "text-destructive"}`}>
                                                        {delta >= 0 ? "+" : ""}
                                                        {formatCredits(delta)}
                                                    </TableCell>
                                                    <TableCell className="text-right">{formatCredits(entry.balance_after)}</TableCell>
                                                    <TableCell className="text-right">
                                                        {formatAmount(entry.amount_minor, entry.amount_currency)}
                                                    </TableCell>
                                                </TableRow>
                                            );
                                        })}
                                    </TableBody>
                                </Table>
                            </div>
                        ) : (
                            <div className="rounded-lg border border-dashed p-8 text-center text-muted-foreground">
                                No ledger entries yet
                            </div>
                        )}
                        {ledgerTotalPages > 1 && (
                            <div className="flex items-center justify-between mt-6">
                                <p className="text-sm text-muted-foreground">
                                    Page {ledgerPage} of {ledgerTotalPages} ({ledgerTotalCount} total entries)
                                </p>
                                <div className="flex gap-2">
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => handlePageChange(ledgerPage - 1)}
                                        disabled={ledgerPage <= 1 || loading || refreshing}
                                    >
                                        <ChevronLeft className="h-4 w-4" />
                                        Previous
                                    </Button>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => handlePageChange(ledgerPage + 1)}
                                        disabled={ledgerPage >= ledgerTotalPages || loading || refreshing}
                                    >
                                        Next
                                        <ChevronRight className="h-4 w-4" />
                                    </Button>
                                </div>
                            </div>
                        )}
                    </CardContent>
                </Card>
            ) : (
                <Card>
                    <CardHeader>
                        <CardTitle>Credit Usage</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <Progress value={usagePercent} />
                        <div className="flex justify-between text-sm text-muted-foreground">
                            <span>{usagePercent}% used</span>
                            <span>{formatCredits(remainingCredits)} of {formatCredits(totalQuota)} remaining</span>
                        </div>
                    </CardContent>
                </Card>
            )}
        </div>
    );
}
