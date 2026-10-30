import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import InvoicesPage from "./page";

const mocks = vi.hoisted(() => ({
    listPayments: vi.fn(), downloadInvoice: vi.fn(), toastError: vi.fn(), toastSuccess: vi.fn(),
    copy: vi.fn(),
    auth: { isAuthenticated: true, loading: false },
    config: { deploymentMode: "saas" },
}));
vi.mock("@/client/sdk.gen", () => ({
    getBillingPaymentsApiV1OrganizationsBillingPaymentsGet: mocks.listPayments,
    downloadBillingInvoiceApiV1OrganizationsBillingInvoicesPaymentOrderIdPdfGet: mocks.downloadInvoice,
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => mocks.auth }));
vi.mock("@/context/AppConfigContext", () => ({
    useAppConfig: () => ({ config: mocks.config, loading: false }),
}));
vi.mock("@/context/OrgConfigContext", () => ({
    useOrgConfig: () => ({ orgContext: { organization_id: 42 }, loading: false }),
}));
vi.mock("@/hooks/useOrganizationTimezone", () => ({ useOrganizationTimezone: () => "UTC" }));
vi.mock("@/lib/clipboard", () => ({ copyTextToClipboard: mocks.copy }));
vi.mock("sonner", () => ({ toast: { error: mocks.toastError, success: mocks.toastSuccess } }));

const payments = [
    {
        payment_order_id: 203, paid_at: "2026-10-30T09:30:00Z", amount_minor: 5000, currency: "USD",
        gateway_payment_id: "pay_new", invoice_number: "DC-2627-102", invoice_status: "issued",
    },
    {
        payment_order_id: 202, paid_at: "2026-10-30T10:00:00Z", amount_minor: 1000, currency: "USD",
        gateway_payment_id: "pay_now", invoice_number: null, invoice_status: "pending",
    },
    {
        payment_order_id: 201, paid_at: "2026-09-02T09:30:00Z", amount_minor: 2500, currency: "USD",
        gateway_payment_id: "pay_old", invoice_number: null, invoice_status: "unavailable",
    },
];

beforeEach(() => {
    vi.clearAllMocks();
    mocks.config.deploymentMode = "saas";
    mocks.listPayments.mockResolvedValue({ data: { payments } });
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:invoice") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
});

describe("invoices page", () => {
    it("lists payments with a download, a pending note, or an email request", async () => {
        render(<InvoicesPage />);

        expect(await screen.findByRole("button", { name: "DC-2627-102" })).toBeTruthy();
        expect(screen.getByText("Being prepared")).toBeTruthy();
        expect(screen.getByRole("button", { name: "Email us" })).toBeTruthy();
    });

    it("shows where to email for an older invoice, with the payment details", async () => {
        mocks.copy.mockResolvedValue(undefined);
        render(<InvoicesPage />);
        fireEvent.click(await screen.findByRole("button", { name: "Email us" }));

        const dialog = await screen.findByRole("dialog");
        expect(dialog.textContent).toContain("founders@dograh.com");
        expect(dialog.textContent).toContain("$25.00");
        expect(dialog.textContent).toContain("pay_old");
        fireEvent.click(screen.getByRole("button", { name: "Copy" }));
        await waitFor(() => expect(mocks.toastSuccess).toHaveBeenCalledWith("Email address copied"));
        expect(mocks.copy).toHaveBeenCalledWith("founders@dograh.com");
    });

    it("downloads the invoice PDF under its number", async () => {
        mocks.downloadInvoice.mockResolvedValue({ data: new Blob(["%PDF"]) });
        const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
        render(<InvoicesPage />);
        fireEvent.click(await screen.findByRole("button", { name: "DC-2627-102" }));

        await waitFor(() => expect(click).toHaveBeenCalled());
        expect(mocks.downloadInvoice).toHaveBeenCalledWith({
            path: { payment_order_id: 203 }, parseAs: "blob",
        });
        expect((click.mock.contexts[0] as HTMLAnchorElement).download).toBe("DC-2627-102.pdf");
        click.mockRestore();
    });

    it("explains a failed download", async () => {
        mocks.downloadInvoice.mockResolvedValue({ error: { detail: "Invoice not found" } });
        render(<InvoicesPage />);
        fireEvent.click(await screen.findByRole("button", { name: "DC-2627-102" }));

        await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith("Invoice not found"));
    });

    it("shows why payments could not be loaded", async () => {
        mocks.listPayments.mockResolvedValue({ error: { detail: "Could not load payments" } });
        render(<InvoicesPage />);

        expect((await screen.findByRole("alert")).textContent).toBe("Could not load payments");
    });

    it("does not ask for payments in OSS mode", async () => {
        mocks.config.deploymentMode = "oss";
        render(<InvoicesPage />);

        expect(await screen.findByText(/available there/)).toBeTruthy();
        expect(mocks.listPayments).not.toHaveBeenCalled();
    });
});
