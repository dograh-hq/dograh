import { CheckCircle2, Loader2, Square } from "lucide-react";

import { ConnectionStatus } from "@/app/workflow/[workflowId]/run/[runId]/components";
import { Button } from "@/components/ui/button";

interface SessionStatusProps {
    connectionStatus: "idle" | "connecting" | "connected" | "failed";
    isStarting: boolean;
    ending: boolean;
    error: string | null;
    savedSessionId: string | null;
    onEnd: () => void;
}
export function SessionStatus({
    connectionStatus,
    isStarting,
    ending,
    error,
    savedSessionId,
    onEnd,
}: SessionStatusProps) {
    return (
        <div className="space-y-3 rounded-xl border bg-card p-4 shadow-sm">
            <ConnectionStatus connectionStatus={connectionStatus} />
            {error ? <p className="text-sm text-destructive">{error}</p> : null}
            {savedSessionId ? (
                <p className="flex items-center gap-2 text-sm text-emerald-600">
                    <CheckCircle2 className="h-4 w-4" />
                    Saved session {savedSessionId}
                </p>
            ) : (
                <Button
                    type="button"
                    variant="destructive"
                    onClick={onEnd}
                    disabled={ending || isStarting}
                    className="w-full sm:w-auto"
                >
                    {ending ? <Loader2 className="animate-spin" /> : <Square />}
                    {ending ? "Saving..." : "End Session"}
                </Button>
            )}
        </div>
    );
}
