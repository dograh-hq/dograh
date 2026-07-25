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

    let copiedText = false;
    const handleCopy = (event: ClipboardEvent) => {
        if (!event.clipboardData) {
            return;
        }

        event.clipboardData.setData("text/plain", text);
        event.preventDefault();
        copiedText = true;
    };

    document.addEventListener("copy", handleCopy);

    try {
        if (!document.execCommand("copy") || !copiedText) {
            throw new Error("Copy command was unsuccessful");
        }
    } finally {
        document.removeEventListener("copy", handleCopy);
    }
}
