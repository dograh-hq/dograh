import { describe, expect, it } from "vitest";

import { formatRoundingPolicy } from "@/lib/billingDisplay";

describe("formatRoundingPolicy", () => {
    it.each([
        ["ceil_minute", "Rounded up to whole minutes per call."],
        ["ceil_1_minute", "Rounded up to whole minutes per call."],
        ["ceil_15_seconds", "Rounded up in 15-second increments per call."],
        ["ceil_30_second", "Rounded up in 30-second increments per call."],
        ["ceil_2_minutes", "Rounded up in 2-minute increments per call."],
        ["none", "Billed using exact measured usage."],
        ["custom_contract", "Billing policy: custom contract."],
        ["", "Billing policy details are unavailable."],
    ])("formats %s", (policy, expected) => {
        expect(formatRoundingPolicy(policy)).toBe(expected);
    });
});
