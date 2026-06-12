"use client";

import { CircleDollarSign, CreditCard, RefreshCw } from "lucide-react";
import Link from "next/link";
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
import { useAuth } from "@/lib/auth";

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

const formatDate = (value: string) => (
    new Date(value).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
    })
);

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

export default function BillingPage() {
    const auth = useAuth();
    const { config } = useAppConfig();
    const [credits, setCredits] = useState<MpsBillingCreditsResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [purchasing, setPurchasing] = useState(false);

    const isBillingV2 = credits?.billing_version === "v2";
    const canPurchaseCredits = isBillingV2 && config?.deploymentMode !== "oss";
    const totalQuota = credits?.total_quota ?? 0;
    const remainingCredits = credits?.remaining_credits ?? 0;
    const usedCredits = credits?.total_credits_used ?? 0;
    const usagePercent = totalQuota > 0 ? Math.min(100, Math.round((usedCredits / totalQuota) * 100)) : 0;

    const ledgerEntries = useMemo(() => credits?.ledger_entries ?? [], [credits?.ledger_entries]);

    const fetchCredits = useCallback(async ({ silent = false }: { silent?: boolean } = {}) => {
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
                query: { limit: 50 },
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
        fetchCredits();
    }, [fetchCredits]);

    const handleRefresh = () => {
        fetchCredits({ silent: true });
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

    if (loading) {
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

            <div className="grid gap-4 md:grid-cols-2">
                <Card>
                    <CardHeader className="pb-2">
                        <CardDescription>{isBillingV2 ? "Credit balance" : "Credits remaining"}</CardDescription>
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
                            {isBillingV2 ? "Recent ledger debit total" : "Current allocation usage"}
                        </p>
                    </CardContent>
                </Card>
            </div>

            {isBillingV2 ? (
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
                                                    <TableCell>{formatDate(entry.created_at)}</TableCell>
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
