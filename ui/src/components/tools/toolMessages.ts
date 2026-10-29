import type { HttpApiToolDefinition, ToolStartAudioMessage, ToolStartTextMessage } from "@/client/types.gen";

export type ToolMessages = NonNullable<HttpApiToolDefinition["messages"]>;
export type MessageVariant = Pick<ToolStartTextMessage, "type" | "text"> | Pick<ToolStartAudioMessage, "type" | "recording_id">;

// Groups are an editing convenience; the API stores a flat list of messages.
export type ToolMessagesForm = {
    start: { variants: MessageVariant[] } | null;
    delayed: { after_ms: number; variants: MessageVariant[] }[];
};

export function loadToolMessages(definition: Record<string, unknown>): ToolMessagesForm {
    const messages = (definition.messages ?? []) as ToolMessages;
    const start: MessageVariant[] = [];
    const delayed = new Map<number, MessageVariant[]>();
    for (const message of messages) {
        const variant: MessageVariant = message.type === "text"
            ? { type: "text", text: message.text }
            : { type: "audio", recording_id: message.recording_id };
        if (message.event === "start") start.push(variant);
        else delayed.set(message.after_ms, [...(delayed.get(message.after_ms) ?? []), variant]);
    }
    return {
        start: start.length ? { variants: start } : null,
        delayed: [...delayed].sort(([a], [b]) => a - b).map(([after_ms, variants]) => ({ after_ms, variants })),
    };
}

export function serializeToolMessages(form: ToolMessagesForm): ToolMessages {
    return [
        ...(form.start?.variants ?? []).map(variant => ({ ...variant, event: "start" as const })),
        ...[...form.delayed].sort((a, b) => a.after_ms - b.after_ms).flatMap(group =>
            group.variants.map(variant => ({ ...variant, event: "delayed" as const, after_ms: group.after_ms }))),
    ];
}

export function singleStartMessages(type: "none" | "custom" | "audio", text = "", recordingId = ""): ToolMessages {
    if (type === "none") return [];
    return type === "audio"
        ? [{ event: "start", type: "audio", recording_id: Number(recordingId) }]
        : [{ event: "start", type: "text", text }];
}

export function validateToolMessages(messages: ToolMessagesForm): string | null {
    const delayed = messages.delayed;
    if (delayed.length > 10) return "You can add up to 10 delayed updates.";
    if (delayed.some(({ after_ms }) => !Number.isInteger(after_ms) || after_ms <= 0 || after_ms > 120000)) {
        return "Each delay must be a whole number between 1 and 120000 milliseconds.";
    }
    if (new Set(delayed.map(({ after_ms }) => after_ms)).size !== delayed.length) {
        return "Each delayed update must have a different timing. Add variants within the same update.";
    }
    for (const group of [messages.start, ...delayed]) {
        if (!group) continue;
        if (!group.variants.length || group.variants.length > 20) {
            return "Each message group must contain between 1 and 20 variants.";
        }
        for (const variant of group.variants) {
            if (variant.type === "text" && !variant.text.trim()) {
                return "Enter text for each variant, or remove it.";
            }
            if (variant.type === "audio" && (!Number.isInteger(variant.recording_id) || variant.recording_id <= 0)) {
                return "Select a recording for each audio variant, or remove it.";
            }
        }
    }
    return null;
}
