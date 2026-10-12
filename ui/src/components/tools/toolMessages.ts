import type { HttpApiToolDefinition, ToolMessages, ToolMessageVariants } from "@/client/types.gen";

export type MessageVariant = ToolMessageVariants["variants"][number];

export function loadToolMessages(definition: Record<string, unknown>): ToolMessages {
    if (definition.messages != null) return definition.messages as ToolMessages;
    if (definition.type !== "http_api") return {};
    const config = definition.config as HttpApiToolDefinition["config"] | undefined;
    if (config?.customMessageType === "audio") {
        const recordingId = Number(config.customMessageRecordingId);
        return recordingId > 0
            ? { start: { variants: [{ type: "audio", recording_id: recordingId }] } }
            : {};
    }
    return config?.customMessage?.trim()
        ? { start: { variants: [{ type: "text", text: config.customMessage }] } }
        : {};
}

export function validateToolMessages(messages: ToolMessages): string | null {
    const delayed = messages.delayed ?? [];
    if (delayed.length > 10) return "You can add up to 10 delayed updates.";
    if (delayed.some(({ after_ms }) => !Number.isInteger(after_ms) || after_ms <= 0 || after_ms > 120000)) {
        return "Each delay must be a whole number between 1 and 120000 milliseconds.";
    }
    if (new Set(delayed.map(({ after_ms }) => after_ms)).size !== delayed.length) {
        return "Each delayed update must have a different timing.";
    }
    for (const group of [messages.start, ...delayed]) {
        if (!group) continue;
        if (!group.variants.length || group.variants.length > 20) {
            return "Each message group must contain between 1 and 20 variants.";
        }
        for (const variant of group.variants) {
            if (variant.type === "text" && (!variant.text.trim() || variant.text.trim().length > 2000)) {
                return "Enter between 1 and 2000 characters for each text variant, or remove it.";
            }
            if (variant.type === "audio" && (!Number.isInteger(variant.recording_id) || variant.recording_id <= 0)) {
                return "Select a recording for each audio variant, or remove it.";
            }
        }
    }
    return null;
}

export function orderedToolMessages(messages: ToolMessages): ToolMessages {
    return { ...messages, delayed: [...(messages.delayed ?? [])].sort((a, b) => a.after_ms - b.after_ms) };
}
