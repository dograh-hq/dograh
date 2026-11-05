import { describe, expect, it } from "vitest";

import { getBillingActivity, getBillingDateRange, getBillingPeriod } from "./billingFilters";

describe("billing filters", () => {
    it("defaults to all activity and all time", () => {
        expect(getBillingActivity(null)).toBe("all");
        expect(getBillingActivity("invalid")).toBe("all");
        expect(getBillingActivity("credit")).toBe("credit");
        expect(getBillingActivity("purchase")).toBe("credit");
        expect(getBillingPeriod(null)).toBe("all");
        expect(getBillingDateRange("all", "UTC", "2026-01-01", "2026-02-01")).toEqual({});
    });

    it("uses the organization's calendar month at a UTC month boundary", () => {
        expect(getBillingDateRange("this_month", "America/Los_Angeles", null, null,
            new Date("2026-07-01T01:00:00Z"))).toEqual({
            start_date: "2026-06-01", end_date: "2026-06-30",
        });
    });

    it("handles year boundaries and leap years", () => {
        expect(getBillingDateRange("last_month", "Asia/Kolkata", null, null,
            new Date("2026-01-15T00:00:00Z"))).toEqual({
            start_date: "2025-12-01", end_date: "2025-12-31",
        });
        expect(getBillingDateRange("last_month", "UTC", null, null,
            new Date("2024-03-15T00:00:00Z"))).toEqual({
            start_date: "2024-02-01", end_date: "2024-02-29",
        });
    });

    it("keeps custom dates as calendar dates for server-side timezone conversion", () => {
        expect(getBillingDateRange("custom", "America/Los_Angeles", "2026-03-08", "2026-03-08"))
            .toEqual({ start_date: "2026-03-08", end_date: "2026-03-08" });
    });
});
