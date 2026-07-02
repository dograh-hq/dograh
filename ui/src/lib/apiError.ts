/**
 * Extract a human-readable message from a backend error response.
 *
 * The generated API client returns `{ error }` on failure (it does not throw),
 * and FastAPI shapes that error as `{ detail: string }`, `{ detail:
 * [{ msg, loc, ... }] }`, or backend validation arrays like `{ detail:
 * [{ model, message }] }`. This normalizes those to a single string so it can
 * be rendered or thrown directly.
 */
function messagesFromItems(items: unknown[]): string[] {
    return items
        .map((item) => {
            if (typeof item === "string") return item;
            if (!item || typeof item !== "object") return null;
            const detail = item as { message?: unknown; msg?: unknown; model?: unknown };
            const message = typeof detail.message === "string"
                ? detail.message
                : typeof detail.msg === "string"
                    ? detail.msg
                    : null;
            if (!message) return null;
            return typeof detail.model === "string" && detail.model
                ? `${detail.model}: ${message}`
                : message;
        })
        .filter((message): message is string => Boolean(message));
}

export function detailFromError(err: unknown, fallback = "Request failed"): string {
    if (typeof err === "string") return err;
    const e = err as { detail?: unknown };
    if (typeof e?.detail === "string") return e.detail;
    if (Array.isArray(e?.detail) && e.detail.length > 0) {
        const messages = messagesFromItems(e.detail);
        if (messages.length > 0) return messages.join("\n");
    }
    // Trigger-path conflicts and node-instance validation arrive as an object:
    // { is_valid: false, errors: [{ message, ... }] }
    if (e?.detail && typeof e.detail === "object") {
        const nested = (e.detail as { errors?: unknown }).errors;
        if (Array.isArray(nested) && nested.length > 0) {
            const messages = messagesFromItems(nested);
            if (messages.length > 0) return messages.join("\n");
        }
    }
    return fallback;
}
