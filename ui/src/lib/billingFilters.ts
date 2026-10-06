export type BillingPeriod = "all" | "this_month" | "last_month" | "custom";
export type BillingActivity = "credit" | "debit" | "all";

export function getBillingActivity(value: string | null): BillingActivity {
    if (value === "purchase") return "credit";
    return value === "credit" || value === "debit" ? value : "all";
}

export function getBillingPeriod(value: string | null): BillingPeriod {
    return value === "this_month" || value === "last_month" || value === "custom"
        ? value : "all";
}

export function getBillingDateRange(
    period: BillingPeriod,
    timezone: string,
    startDate: string | null = null,
    endDate: string | null = null,
    now = new Date(),
): { start_date?: string; end_date?: string } {
    if (period === "all") return {};
    if (period === "custom") {
        return { start_date: startDate || undefined, end_date: endDate || undefined };
    }

    const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: timezone, year: "numeric", month: "numeric",
    }).formatToParts(now);
    const year = Number(parts.find(part => part.type === "year")?.value);
    const month = Number(parts.find(part => part.type === "month")?.value) - 1
        - (period === "last_month" ? 1 : 0);
    // UTC is only used for calendar arithmetic here. The API resolves these
    // calendar dates to instants in the organization's timezone.
    return {
        start_date: new Date(Date.UTC(year, month, 1)).toISOString().slice(0, 10),
        end_date: new Date(Date.UTC(year, month + 1, 0)).toISOString().slice(0, 10),
    };
}
