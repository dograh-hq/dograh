import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { formatDate } from "@/lib/dateTime";

import InvoicesPage from "./page";

const mocks = vi.hoisted(() => ({
    listPayments: vi.fn(), downloadInvoice: vi.fn(), toastError: vi.fn(), toastSuccess: vi.fn(),
    copy: vi.fn(),
    auth: { isAuthenticated: true, loading: false, redirectToLogin: vi.fn() },
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

// The page formats amounts in the runtime's locale.
const money = (amount: number, currency = "USD") =>
    new Intl.NumberFormat(undefined, { style: "currency", currency }).format(amount);

// Newest first, as the API returns them.
const payments = [
    {
        payment_order_id: 203, paid_at: "2026-10-30T10:00:00Z", amount_minor: 1000, currency: "USD",
        gateway_payment_id: "pay_now", invoice_number: null, invoice_status: "pending",
    },
    {
        payment_order_id: 202, paid_at: "2026-10-28T09:30:00Z", amount_minor: 5000, currency: "USD",
        gateway_payment_id: "pay_new", invoice_number: "DC-2627-102", invoice_status: "issued",
    },
    {
        payment_order_id: 201, paid_at: "2026-09-02T09:30:00Z", amount_minor: 2500, currency: "USD",
        gateway_payment_id: "pay_old", invoice_number: null, invoice_status: "unavailable",
    },
];

beforeEach(() => {
    vi.clearAllMocks();
    mocks.auth.isAuthenticated = true;
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
        const amounts = (screen.getAllByRole("row") as HTMLTableRowElement[])
            .slice(1)
            .map((row) => row.cells[1].textContent);
        expect(amounts).toEqual([money(10), money(50), money(25)]);
    });

    it("formats amounts in currencies without cents", async () => {
        mocks.listPayments.mockResolvedValue({
            data: { payments: [{ ...payments[0], amount_minor: 1500, currency: "JPY" }] },
        });
        render(<InvoicesPage />);

        expect(await screen.findByText(money(1500, "JPY"))).toBeTruthy();
        expect(screen.queryByText(/15\.00/)).toBeNull();
    });

    it("asks for an email request when an issued invoice has no number", async () => {
        mocks.listPayments.mockResolvedValue({
            data: { payments: [{ ...payments[1], invoice_number: null }] },
        });
        render(<InvoicesPage />);

        expect(await screen.findByRole("button", { name: "Email us" })).toBeTruthy();
        expect(screen.queryByRole("button", { name: /null/ })).toBeNull();
    });

    it("shows where to email for an older invoice, with the payment details", async () => {
        mocks.copy.mockResolvedValue(undefined);
        render(<InvoicesPage />);
        fireEvent.click(await screen.findByRole("button", { name: "Email us" }));

        const dialog = await screen.findByRole("dialog");
        expect(dialog.textContent).toContain("founders@dograh.com");
        expect(dialog.textContent).toContain(formatDate("2026-09-02T09:30:00Z", "UTC"));
        expect(dialog.textContent).toContain(money(25));
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
            path: { payment_order_id: 202 }, parseAs: "blob",
        });
        expect((click.mock.contexts[0] as HTMLAnchorElement).download).toBe("DC-2627-102.pdf");
        click.mockRestore();
    });

    it("allows one download at a time", async () => {
        mocks.downloadInvoice.mockReturnValue(new Promise(() => {}));
        mocks.listPayments.mockResolvedValue({
            data: { payments: [payments[1], { ...payments[1], payment_order_id: 204, invoice_number: "DC-2627-103" }] },
        });
        render(<InvoicesPage />);
        fireEvent.click(await screen.findByRole("button", { name: "DC-2627-102" }));

        await waitFor(() => expect(
            (screen.getByRole("button", { name: "DC-2627-103" }) as HTMLButtonElement).disabled,
        ).toBe(true));
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

    it("sends signed-out visitors to log in", async () => {
        mocks.auth.isAuthenticated = false;
        render(<InvoicesPage />);

        await waitFor(() => expect(mocks.auth.redirectToLogin).toHaveBeenCalled());
        expect(mocks.listPayments).not.toHaveBeenCalled();
    });

    it("does not ask for payments in OSS mode", async () => {
        mocks.config.deploymentMode = "oss";
        render(<InvoicesPage />);

        expect(await screen.findByText(/available there/)).toBeTruthy();
        expect(screen.getByRole("link", { name: "app.dograh.com" }).getAttribute("href"))
            .toBe("https://app.dograh.com/billing/invoices");
        expect(mocks.listPayments).not.toHaveBeenCalled();
    });
});
