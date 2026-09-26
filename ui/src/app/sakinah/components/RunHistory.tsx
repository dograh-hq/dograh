"use client";

import { useEffect, useState } from "react";

import { MediaPreviewButton, MediaPreviewDialog } from "@/components/MediaPreviewDialog";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth";
import {
    listSakinahRuns,
    type PersistedSakinahRun,
} from "@/lib/sakinahPersistence";

interface SakinahRunHistoryProps {
    refreshKey?: number;
}

function nestedArtifact(
    run: PersistedSakinahRun,
    role: "sakinah" | "service_user",
): Record<string, unknown> {
    const roleReference = run.recording_file_reference[role];
    return roleReference && typeof roleReference === "object"
        ? roleReference as Record<string, unknown>
        : {};
}

function nestedRecordingKey(run: PersistedSakinahRun): string | null {
    for (const role of ["sakinah", "service_user"] as const) {
        const reference = nestedArtifact(run, role);
        if (typeof reference.recording_url === "string") return reference.recording_url;
        const recordings = reference.recording_file_reference;
        if (!recordings || typeof recordings !== "object") continue;
        const mixed = (recordings as Record<string, unknown>).mixed;
        if (typeof mixed === "string") return mixed;
        if (mixed && typeof mixed === "object" && typeof (mixed as Record<string, unknown>).storage_key === "string") {
            return (mixed as Record<string, unknown>).storage_key as string;
        }
    }
    const mixed = run.recording_file_reference.mixed;
    if (typeof mixed === "string") return mixed;
    if (mixed && typeof mixed === "object" && typeof (mixed as Record<string, unknown>).storage_key === "string") {
        return (mixed as Record<string, unknown>).storage_key as string;
    }
    return null;
}

function nestedTranscriptKey(run: PersistedSakinahRun): string | null {
    for (const role of ["sakinah", "service_user"] as const) {
        const reference = nestedArtifact(run, role);
        if (typeof reference.transcript_url === "string") return reference.transcript_url;
    }
    return null;
}

function previewTurns(run: PersistedSakinahRun): Array<Record<string, unknown>> {
    const turns = run.preview_data.turns;
    return Array.isArray(turns)
        ? turns.filter((turn): turn is Record<string, unknown> => Boolean(turn && typeof turn === "object"))
        : [];
}

export function SakinahRunHistory({ refreshKey = 0 }: SakinahRunHistoryProps) {
    const { loading: authLoading, user, redirectToLogin } = useAuth();
    const [runs, setRuns] = useState<PersistedSakinahRun[]>([]);
    const [error, setError] = useState<string | null>(null);
    const mediaPreview = MediaPreviewDialog();

    useEffect(() => {
        let active = true;
        if (authLoading) return () => {
            active = false;
        };
        if (!user) {
            redirectToLogin();
            return () => {
                active = false;
            };
        }
        setError(null);
        void listSakinahRuns()
            .then((loadedRuns) => {
                if (active) setRuns(loadedRuns);
            })
            .catch((loadError) => {
                if (active) {
                    setError(loadError instanceof Error ? loadError.message : "Unable to load previous runs.");
                }
            });
        return () => {
            active = false;
        };
    }, [authLoading, redirectToLogin, refreshKey, user]);

    return (
        <Card>
            <CardHeader><CardTitle>Previous agent runs</CardTitle></CardHeader>
            <CardContent className="space-y-4">
                {error ? <p className="text-sm text-destructive">{error}</p> : null}
                {!error && runs.length === 0 ? (
                    <p className="text-sm text-muted-foreground">Completed sessions will appear here for this account.</p>
                ) : null}
                {runs.map((run) => {
                    const recordingKey = run.recording_url ?? nestedRecordingKey(run);
                    const transcriptKey = run.transcript_url ?? nestedTranscriptKey(run);
                    const turns = previewTurns(run);
                    return (
                        <article key={run.session_id} className="space-y-3 rounded-lg border p-4">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <div>
                                    <p className="font-medium">{run.scenario.slice(0, 120)}{run.scenario.length > 120 ? "…" : ""}</p>
                                    <p className="text-xs text-muted-foreground">
                                        Run #{run.run_id} · {new Date(run.started_at).toLocaleString()} · {run.status}
                                    </p>
                                </div>
                                <MediaPreviewButton
                                    recordingUrl={recordingKey}
                                    transcriptUrl={transcriptKey}
                                    runId={run.run_id}
                                    onOpenPreview={mediaPreview.openPreview}
                                />
                            </div>
                            <details className="rounded-md border bg-muted/20 p-3">
                                <summary className="cursor-pointer text-sm font-medium">
                                    Conversation preview{turns.length ? ` · ${turns.length} turns` : ""}
                                </summary>
                                {run.transcript ? (
                                    <pre className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap rounded bg-muted/50 p-3 text-xs">
                                        {run.transcript}
                                    </pre>
                                ) : turns.length ? (
                                    <div className="mt-3 space-y-2">
                                        {turns.map((turn, index) => (
                                            <p key={`${run.session_id}-turn-${index}`} className="whitespace-pre-wrap text-sm">
                                                <span className="font-medium">{String(turn.role ?? "unknown")}:</span>{" "}
                                                {String(turn.text ?? "")}
                                            </p>
                                        ))}
                                    </div>
                                ) : (
                                    <p className="mt-3 text-sm text-muted-foreground">No transcript turns were captured.</p>
                                )}
                            </details>
                        </article>
                    );
                })}
            </CardContent>
            {mediaPreview.dialog}
        </Card>
    );
}
