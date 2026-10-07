import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { BillingActivity, BillingPeriod } from "@/lib/billingFilters";
import { getBillingDateRange } from "@/lib/billingFilters";

interface BillingLedgerFiltersProps {
    activity: BillingActivity;
    period: BillingPeriod;
    startDate: string | null;
    endDate: string | null;
    timezone: string;
    onChange: (updates: Record<string, string | null>) => void;
}

export function BillingLedgerFilters({
    activity, period, startDate, endDate, timezone, onChange,
}: BillingLedgerFiltersProps) {
    const [from, setFrom] = useState(startDate || "");
    const [to, setTo] = useState(endDate || "");

    const changePeriod = (value: BillingPeriod) => {
        const dates = value === "custom" ? getBillingDateRange("this_month", timezone) : {};
        onChange({
            period: value === "all" ? null : value,
            start_date: dates.start_date ?? null,
            end_date: dates.end_date ?? null,
        });
    };

    return (
        <div className="mb-4 space-y-3">
            <div className="flex flex-wrap items-end gap-3">
                <div className="space-y-2">
                    <Label htmlFor="billing-activity">Activity</Label>
                    <Select value={activity} onValueChange={value => onChange({ activity: value })}>
                        <SelectTrigger id="billing-activity" className="w-40"><SelectValue /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All activity</SelectItem>
                            <SelectItem value="credit">Credit</SelectItem>
                            <SelectItem value="debit">Usage</SelectItem>
                        </SelectContent>
                    </Select>
                </div>
                <div className="space-y-2">
                    <Label htmlFor="billing-period">Date range</Label>
                    <Select value={period} onValueChange={changePeriod}>
                        <SelectTrigger id="billing-period" className="w-40"><SelectValue /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All time</SelectItem>
                            <SelectItem value="this_month">This month</SelectItem>
                            <SelectItem value="last_month">Last month</SelectItem>
                            <SelectItem value="custom">Custom</SelectItem>
                        </SelectContent>
                    </Select>
                </div>
            </div>
            {period === "custom" && (
                <form className="flex flex-wrap items-end gap-3" onSubmit={event => {
                    event.preventDefault();
                    if (from && to && from <= to) {
                        onChange({ start_date: from, end_date: to });
                    }
                }}>
                    <div className="space-y-2">
                        <Label htmlFor="billing-from">From</Label>
                        <Input id="billing-from" type="date" required value={from}
                            max={to || undefined} onChange={event => setFrom(event.target.value)} />
                    </div>
                    <div className="space-y-2">
                        <Label htmlFor="billing-to">To</Label>
                        <Input id="billing-to" type="date" required value={to}
                            min={from || undefined} onChange={event => setTo(event.target.value)} />
                    </div>
                    <Button type="submit" variant="outline">Apply dates</Button>
                </form>
            )}
            <p className="text-xs text-muted-foreground">Dates and times shown in {timezone}.</p>
        </div>
    );
}
