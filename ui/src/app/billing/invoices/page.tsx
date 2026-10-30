"use client";

import { ArrowLeft, Copy, Download, Loader2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
    downloadBillingInvoiceApiV1OrganizationsBillingInvoicesPaymentOrderIdPdfGet,
    getBillingPaymentsApiV1OrganizationsBillingPaymentsGet,
} from "@/client/sdk.gen";
import type { BillingPaymentResponse } from "@/client/types.gen";
import { PageLayout } from "@/components/layout/PageLayout";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAppConfig } from "@/context/AppConfigContext";
import { useOrgConfig } from "@/context/OrgConfigContext";
import { useOrganizationTimezone } from "@/hooks/useOrganizationTimezone";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { copyTextToClipboard } from "@/lib/clipboard";
import { formatDate } from "@/lib/dateTime";

// Payments from before automatic invoicing were invoiced by hand; customers
// ask for those by email. Shown rather than linked: mailto links do nothing
// for people without an email app.
const INVOICE_REQUEST_EMAIL = "founders@dograh.com";

const formatAmount = (amountMinor: number, currency: string) => (
    new Intl.NumberFormat(undefined, { style: "currency", currency }).format(amountMinor / 100)
);

export default function InvoicesPage() {
    const auth = useAuth();
    const { config, loading: configLoading } = useAppConfig();
    const { orgContext, loading: orgLoading } = useOrgConfig();
    const timezone = useOrganizationTimezone();
    const [payments, setPayments] = useState<BillingPaymentResponse[] | null>(null);
    const [fetchError, setFetchError] = useState<string | null>(null);
    const [downloading, setDownloading] = useState<number | null>(null);
    const [requesting, setRequesting] = useState<BillingPaymentResponse | null>(null);
    const isOssMode = !configLoading && config?.deploymentMode === "oss";

    useEffect(() => {
        if (auth.loading || configLoading || orgLoading || isOssMode) {
            return;
        }
        if (!auth.isAuthenticated) {
            setPayments([]);
            return;
        }

        const controller = new AbortController();
        const fetchPayments = async () => {
            setPayments(null);
            setFetchError(null);
            try {
                const response = await getBillingPaymentsApiV1OrganizationsBillingPaymentsGet({
                    signal: controller.signal,
                });
                if (response.error || !response.data) {
                    throw new Error(detailFromError(response.error, "Could not load payments"));
                }
                if (!controller.signal.aborted) setPayments(response.data.payments);
            } catch (error) {
                if (controller.signal.aborted) return;
                setFetchError(error instanceof Error ? error.message : "Could not load payments");
            }
        };
        void fetchPayments();
        return () => controller.abort();
    }, [auth.isAuthenticated, auth.loading, configLoading, orgLoading,
        orgContext?.organization_id, isOssMode]);

    const downloadInvoice = async (payment: BillingPaymentResponse) => {
        setDownloading(payment.payment_order_id);
        try {
            const response = await downloadBillingInvoiceApiV1OrganizationsBillingInvoicesPaymentOrderIdPdfGet({
                path: { payment_order_id: payment.payment_order_id },
                parseAs: "blob",
            });
            if (response.error || !response.data) {
                throw new Error(detailFromError(response.error, "Could not download invoice"));
            }
            const url = URL.createObjectURL(response.data as Blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = `${payment.invoice_number}.pdf`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
        } catch (error) {
            toast.error(error instanceof Error ? error.message : "Could not download invoice");
        } finally {
            setDownloading(null);
        }
    };

    const copyRequestEmail = async () => {
        try {
            await copyTextToClipboard(INVOICE_REQUEST_EMAIL);
            toast.success("Email address copied");
        } catch {
            toast.error("Could not copy the email address");
        }
    };

    return (
        <PageLayout title="Invoices" description="Your credit purchases and their tax invoices."
            actions={<Button variant="outline" asChild>
                <Link href="/billing">
                    <ArrowLeft className="h-4 w-4 mr-2" />
                    Billing
                </Link>
            </Button>}>
            {isOssMode ? (
                <p className="text-muted-foreground">
                    Invoices for credits bought on app.dograh.com are available there.
                </p>
            ) : fetchError ? (
                <p role="alert" className="py-8 text-center text-destructive">{fetchError}</p>
            ) : payments === null ? (
                <Skeleton className="h-64 w-full" />
            ) : payments.length === 0 ? (
                <div className="rounded-lg border border-dashed p-8 text-center text-muted-foreground">
                    No payments yet.
                </div>
            ) : (
                <div className="bg-card border rounded-lg overflow-x-auto shadow-sm">
                    <Table>
                        <TableHeader>
                            <TableRow className="bg-muted/50">
                                <TableHead>Date</TableHead>
                                <TableHead className="text-right">Amount</TableHead>
                                <TableHead className="text-right">Invoice</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {payments.map((payment) => (
                                <TableRow key={payment.payment_order_id}>
                                    <TableCell>{formatDate(payment.paid_at, timezone)}</TableCell>
                                    <TableCell className="text-right">
                                        {formatAmount(payment.amount_minor, payment.currency)}
                                    </TableCell>
                                    <TableCell className="text-right">
                                        {payment.invoice_status === "issued" ? (
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                className="h-8 px-2"
                                                disabled={downloading === payment.payment_order_id}
                                                onClick={() => downloadInvoice(payment)}
                                            >
                                                {downloading === payment.payment_order_id ? (
                                                    <Loader2 className="h-4 w-4 animate-spin" />
                                                ) : (
                                                    <Download className="h-4 w-4" />
                                                )}
                                                {payment.invoice_number}
                                            </Button>
                                        ) : payment.invoice_status === "pending" ? (
                                            <span className="text-sm text-muted-foreground">Being prepared</span>
                                        ) : (
                                            <Button
                                                variant="link"
                                                size="sm"
                                                className="h-8 px-2"
                                                onClick={() => setRequesting(payment)}
                                            >
                                                Email us
                                            </Button>
                                        )}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            )}
            <Dialog open={requesting !== null} onOpenChange={(open) => !open && setRequesting(null)}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>Request this invoice</DialogTitle>
                        <DialogDescription>
                            Email us with these payment details and we&apos;ll send you the invoice.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="flex items-center justify-between gap-2 rounded-md border px-3 py-2">
                        <span className="font-medium">{INVOICE_REQUEST_EMAIL}</span>
                        <Button variant="outline" size="sm" onClick={copyRequestEmail}>
                            <Copy className="h-4 w-4 mr-2" />
                            Copy
                        </Button>
                    </div>
                    {requesting && (
                        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                            <dt className="text-muted-foreground">Date</dt>
                            <dd>{formatDate(requesting.paid_at, timezone)}</dd>
                            <dt className="text-muted-foreground">Amount</dt>
                            <dd>{formatAmount(requesting.amount_minor, requesting.currency)}</dd>
                            {requesting.gateway_payment_id && (
                                <>
                                    <dt className="text-muted-foreground">Payment ID</dt>
                                    <dd className="font-mono">{requesting.gateway_payment_id}</dd>
                                </>
                            )}
                        </dl>
                    )}
                </DialogContent>
            </Dialog>
        </PageLayout>
    );
}
