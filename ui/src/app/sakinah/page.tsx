"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { createSessionApiV1SakinahSessionsPost } from "@/client";
import { MediaPreviewButton, MediaPreviewDialog } from "@/components/MediaPreviewDialog";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { listSakinahRuns, type PersistedSakinahRun } from "@/lib/sakinahPersistence";

import { ActiveSession } from "./components/ActiveSession";
import { ScenarioForm } from "./components/ScenarioForm";
import { TranscriptPanel } from "./components/TranscriptPanel";
import type { SakinahSession } from "./components/types";

function SakinahRunHistory() {
    const [runs, setRuns] = useState<PersistedSakinahRun[]>([]);
    const [error, setError] = useState<string | null>(null);
    const mediaPreview = MediaPreviewDialog();

    useEffect(() => {
        void listSakinahRuns().then(setRuns).catch((loadError) => {
            setError(loadError instanceof Error ? loadError.message : "Unable to load previous runs.");
        });
    }, []);

    return <Card>
        <CardHeader><CardTitle>Previous agent runs</CardTitle></CardHeader>
        <CardContent className="space-y-4">
            {error ? <p className="text-sm text-destructive">{error}</p> : null}
            {!error && runs.length === 0 ? <p className="text-sm text-muted-foreground">Completed sessions will appear here for this account.</p> : null}
            {runs.map((run) => <article key={run.session_id} className="space-y-2 rounded-lg border p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <div><p className="font-medium">{run.scenario.slice(0, 120)}{run.scenario.length > 120 ? "…" : ""}</p><p className="text-xs text-muted-foreground">Run #{run.run_id} · {new Date(run.started_at).toLocaleString()} · {run.status}</p></div>
                    <MediaPreviewButton recordingUrl={run.recording_url} transcriptUrl={run.transcript_url} runId={run.run_id} onOpenPreview={mediaPreview.openPreview} />
                </div>
                {run.transcript ? <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded bg-muted/50 p-3 text-xs">{run.transcript}</pre> : <p className="text-sm text-muted-foreground">Transcript is not available for this run.</p>}
            </article>)}
        </CardContent>
        {mediaPreview.dialog}
    </Card>;
}

export default function SakinahPage() {
    const { getAccessToken } = useAuth();
    const [scenario, setScenario] = useState("");
    const [session, setSession] = useState<SakinahSession | null>(null);
    const [accessToken, setAccessToken] = useState<string | null>(null);
    const [starting, setStarting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [savedSessionId, setSavedSessionId] = useState<string | null>(null);

    const startScenario = async () => {
        setStarting(true);
        setError(null);
        setSavedSessionId(null);
        try {
            // The SDK's auth interceptor signs the request; the token is only
            // needed for the WebRTC signaling WebSocket in ActiveSession.
            const token = await getAccessToken();
            const response = await createSessionApiV1SakinahSessionsPost({
                body: { scenario: scenario.trim() },
            });
            if (response.error || !response.data) {
                throw new Error(
                    detailFromError(response.error, "Unable to create the scenario session."),
                );
            }
            setAccessToken(token);
            setSession({ ...response.data, scenario: scenario.trim() });
        } catch (startError) {
            setError(startError instanceof Error ? startError.message : "Unable to start scenario.");
        } finally {
            setStarting(false);
        }
    };

    const handleSaved = (sessionId: string) => {
        setSavedSessionId(sessionId);
        setSession(null);
        setAccessToken(null);
    };

    return (
        <main className="mx-auto w-full max-w-6xl space-y-6 p-4 md:p-8">
            <header className="space-y-2">
                <p className="text-sm font-medium uppercase tracking-[0.2em] text-primary">Sakinah</p>
                <h1 className="text-3xl font-bold tracking-tight">Scenario Console</h1>
                <p className="max-w-2xl text-muted-foreground">
                    Start a browser voice session and review the conversation as it happens.
                </p>
                <Link
                    href="/sakinah/sim"
                    className="text-sm text-primary underline underline-offset-4"
                >
                    Switch to the AI-to-AI simulation console
                </Link>
            </header>
            {error ? <p className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}</p> : null}
            {savedSessionId ? (
                <p className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 text-sm text-emerald-700">
                    Session saved to your account: {savedSessionId}
                </p>
            ) : null}
            <div className="grid gap-6 lg:grid-cols-3">
                <ScenarioForm
                    scenario={scenario}
                    onScenarioChange={setScenario}
                    onStart={() => void startScenario()}
                    disabled={session !== null}
                    starting={starting}
                />
                {session && accessToken ? (
                    <ActiveSession session={session} accessToken={accessToken} onSaved={handleSaved} />
                ) : (
                    <div className="lg:col-span-2">
                        <TranscriptPanel turns={[]} />
                    </div>
                )}
            </div>
            <SakinahRunHistory />
        </main>
    );
}
