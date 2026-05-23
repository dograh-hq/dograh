export function getErrorMessage(error: unknown) {
    if (error instanceof Error) return error.message;
    return "Something went wrong";
}

export function extractSdkErrorMessage(error: unknown, fallback: string) {
    if (!error) return fallback;
    if (typeof error === "string") return error;
    if (typeof error === "object") {
        const detail = (error as { detail?: unknown }).detail;
        if (typeof detail === "string") return detail;
        if (
            detail &&
            typeof detail === "object" &&
            typeof (detail as { message?: unknown }).message === "string"
        ) {
            return (detail as { message: string }).message;
        }
    }
    return fallback;
}

export function getReplayCursorTurnId(turns: Array<{ id: string }>, turnId: string) {
    const turnIndex = turns.findIndex((turn) => turn.id === turnId);
    if (turnIndex < 0) {
        throw new Error("Turn not found");
    }
    return turns[turnIndex - 1]?.id ?? null;
}
