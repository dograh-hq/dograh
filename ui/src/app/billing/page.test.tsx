import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import BillingPage from "./page";

const mocks = vi.hoisted(() => ({
    getCredits: vi.fn(), push: vi.fn(), error: vi.fn(),
    params: new URLSearchParams(),
    auth: { isAuthenticated: true, loading: false },
    config: { deploymentMode: "saas" },
}));
vi.mock("next/navigation", () => ({
    useRouter: () => ({ push: mocks.push }), useSearchParams: () => mocks.params,
}));
vi.mock("@/client/sdk.gen", () => ({
    getBillingCreditsApiV1OrganizationsBillingCreditsGet: mocks.getCredits,
    createMpsCreditPurchaseUrlApiV1OrganizationsUsageMpsCreditsPurchaseUrlPost: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => mocks.auth }));
vi.mock("@/context/AppConfigContext", () => ({
    useAppConfig: () => ({ config: mocks.config, loading: false }),
}));
vi.mock("@/context/OrgConfigContext", () => ({
    useOrgConfig: () => ({ orgContext: { organization_id: 42 }, loading: false }),
}));
vi.mock("@/hooks/useOrganizationTimezone", () => ({
    useOrganizationTimezone: () => "America/Los_Angeles",
}));
vi.mock("@/lib/metaPixel", () => ({ trackMetaInitiateCheckout: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: mocks.error } }));

const response = {
    data: { remaining_credits: 1000, total_credits_used: 25, ledger_entries: [],
        total_count: 101, total_pages: 3, page: 1 },
};

beforeEach(() => {
    vi.clearAllMocks();
    mocks.params = new URLSearchParams();
    mocks.auth.loading = false;
    mocks.config.deploymentMode = "saas";
    mocks.getCredits.mockResolvedValue(response);
});

describe("billing ledger filters", () => {
    it("waits for auth and defaults to all activity for all time", async () => {
        mocks.auth.loading = true;
        const view = render(<BillingPage />);
        expect(mocks.getCredits).not.toHaveBeenCalled();
        mocks.auth.loading = false;
        view.rerender(<BillingPage />);
        await screen.findByText("Credit Ledger");
        expect(mocks.getCredits.mock.calls[0][0].query).toEqual({
            page: 1, limit: 50, timezone: "America/Los_Angeles",
        });
        expect(screen.getByRole("combobox", { name: "Activity" }).textContent).toBe("All activity");
        expect(screen.getByText("All-time credits used")).toBeTruthy();
    });

    it("resets pagination when selecting Credit", async () => {
        mocks.params = new URLSearchParams("page=3");
        Element.prototype.scrollIntoView = vi.fn();
        render(<BillingPage />);
        await screen.findByText("Credit Ledger");
        fireEvent.keyDown(screen.getByRole("combobox", { name: "Activity" }), { key: "Enter" });
        fireEvent.click(await screen.findByRole("option", { name: "Credit" }));
        expect(mocks.push).toHaveBeenCalledWith("/billing?activity=credit", { scroll: false });
    });

    it("restores URL filters and resets pagination when dates are applied", async () => {
        mocks.params = new URLSearchParams("activity=debit&period=custom&start_date=2026-03-01&end_date=2026-03-31&page=3");
        render(<BillingPage />);
        await screen.findByText("Credit Ledger");
        expect(mocks.getCredits.mock.calls[0][0].query).toEqual({
            page: 3, limit: 50, entry_type: "debit", start_date: "2026-03-01",
            end_date: "2026-03-31", timezone: "America/Los_Angeles",
        });
        fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-03-08" } });
        fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-03-08" } });
        expect(mocks.getCredits).toHaveBeenCalledTimes(1);
        fireEvent.click(screen.getByRole("button", { name: "Apply dates" }));
        expect(mocks.push).toHaveBeenCalledWith(
            "/billing?activity=debit&period=custom&start_date=2026-03-08&end_date=2026-03-08", { scroll: false },
        );
    });

    it("keeps activity and dates when paging, and restores All activity", async () => {
        mocks.params = new URLSearchParams("activity=all&period=custom&start_date=2026-06-01&end_date=2026-06-30");
        render(<BillingPage />);
        await screen.findByText("Credit Ledger");
        expect(mocks.getCredits.mock.calls[0][0].query.entry_type).toBeUndefined();
        fireEvent.click(screen.getByRole("button", { name: "Next" }));
        expect(mocks.push).toHaveBeenCalledWith(
            "/billing?activity=all&period=custom&start_date=2026-06-01&end_date=2026-06-30&page=2", { scroll: false },
        );
    });

    it("ignores stale responses after filters change", async () => {
        let resolveFirst!: (value: typeof response) => void;
        mocks.getCredits.mockReturnValueOnce(new Promise(resolve => { resolveFirst = resolve; }));
        const view = render(<BillingPage />);
        mocks.params = new URLSearchParams("activity=debit");
        view.rerender(<BillingPage />);
        await screen.findByText("Credit Ledger");
        expect(mocks.getCredits.mock.calls[0][0].signal.aborted).toBe(true);
        await act(async () => resolveFirst({ data: { ...response.data, total_credits_used: 999 } }));
        expect(screen.queryByText("999")).toBeNull();
    });

    it("shows filter failures instead of an empty or stale result", async () => {
        mocks.getCredits.mockResolvedValue({ error: { detail: "Invalid billing date range or timezone" } });
        render(<BillingPage />);
        await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Invalid billing date range or timezone"));
        expect(screen.queryByText("No entries match these filters.")).toBeNull();
    });

    it("preserves the OSS credit view without ledger filters", async () => {
        mocks.config.deploymentMode = "oss";
        render(<BillingPage />);
        await screen.findByText("Credits remaining");
        expect(mocks.getCredits.mock.calls[0][0].query).toEqual({ page: 1, limit: 50 });
        expect(screen.queryByLabelText("Activity")).toBeNull();
    });
});
