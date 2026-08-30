"use client";

import Link from "next/link";
import { useState } from "react";

import { client } from "@/client/client.gen";
import { useAuth } from "@/lib/auth";

import { ActiveSession } from "./components/ActiveSession";
import { ScenarioForm } from "./components/ScenarioForm";
import { TranscriptPanel } from "./components/TranscriptPanel";
import type { SakinahSession } from "./components/types";

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
            const token = await getAccessToken();
            const response = await client.post<{
                200: Omit<SakinahSession, "scenario">;
            }>({
                url: "/api/v1/sakinah/sessions",
                headers: { Authorization: `Bearer ${token}` },
                body: { scenario: scenario.trim() },
            });
            if (response.error || !response.data) {
                throw new Error("Unable to create the scenario session.");
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
                    Session saved locally: {savedSessionId}
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
        </main>
    );
}
