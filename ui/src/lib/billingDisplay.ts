export function formatRoundingPolicy(roundingPolicy: string): string {
    const normalizedPolicy = roundingPolicy.trim().toLowerCase();
    if (!normalizedPolicy) {
        return "Billing policy details are unavailable.";
    }
    if (normalizedPolicy === "ceil_minute" || normalizedPolicy === "ceil_1_minute") {
        return "Rounded up to whole minutes per call.";
    }
    if (normalizedPolicy === "none") {
        return "Billed using exact measured usage.";
    }

    const secondsMatch = normalizedPolicy.match(/^ceil_(\d+)_seconds?$/);
    if (secondsMatch) {
        return `Rounded up in ${Number(secondsMatch[1])}-second increments per call.`;
    }

    const minutesMatch = normalizedPolicy.match(/^ceil_(\d+)_minutes?$/);
    if (minutesMatch) {
        return `Rounded up in ${Number(minutesMatch[1])}-minute increments per call.`;
    }

    return `Billing policy: ${roundingPolicy.replaceAll("_", " ")}.`;
}
