export async function copyTextToClipboard(text: string): Promise<void> {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
        try {
            await navigator.clipboard.writeText(text);
            return;
        } catch {
            // Fall back to the legacy copy command when Clipboard API access is denied.
        }
    }

    if (typeof document === "undefined" || typeof document.execCommand !== "function") {
        throw new Error("Clipboard access is unavailable");
    }

    const textArea = document.createElement("textarea");
    textArea.value = text;
    textArea.setAttribute("readonly", "");
    textArea.style.position = "fixed";
    textArea.style.opacity = "0";
    textArea.style.pointerEvents = "none";
    document.body.appendChild(textArea);

    try {
        textArea.select();
        textArea.setSelectionRange(0, text.length);

        if (!document.execCommand("copy")) {
            throw new Error("Copy command was unsuccessful");
        }
    } finally {
        textArea.remove();
    }
}
