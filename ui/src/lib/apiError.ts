/**
 * Extract a human-readable message from a backend error response.
 *
 * The generated API client returns `{ error }` on failure (it does not throw),
 * and FastAPI shapes that error as either `{ detail: string }` (HTTPException)
 * or `{ detail: [{ msg, loc, ... }] }` (422 validation). This normalizes both
 * to a single string so it can be rendered or thrown directly — never pass the
 * raw `detail` to React, as the 422 array crashes rendering.
 */
export function detailFromError(err: unknown, fallback = "Request failed"): string {
    if (typeof err === "string") return err;
    const e = err as { detail?: unknown };
    if (typeof e?.detail === "string") return e.detail;
    if (Array.isArray(e?.detail) && e.detail.length > 0) {
        const first = e.detail[0] as { msg?: string };
        if (first?.msg) return first.msg;
    }
    return fallback;
}
