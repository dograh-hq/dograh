import type { ConversationItem } from "./types";

export function formatConversationValue(value: unknown) {
    if (value == null) {
        return "None";
    }
    if (typeof value === "string") {
        return value;
    }
    try {
        return JSON.stringify(value, null, 2);
    } catch {
        return String(value);
    }
}

export function countConversationMessages(items: ConversationItem[]) {
    return items.filter(
        (item) => item.kind === "message" && item.tone !== "muted",
    ).length;
}
