"use client";

import { ExternalLink, Loader2 } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { pollDograhAccountConnect, startDograhAccountConnect } from "@/client/sdk.gen";
import type { AccountConnectPoll, AccountConnectStart } from "@/client/types.gen";
import { PageSection } from "@/components/layout/PageLayout";
import { Button } from "@/components/ui/button";
import { detailFromError } from "@/lib/apiError";

type State =
    | { step: "idle" }
    | { step: "starting" }
    | { step: "waiting"; request: AccountConnectStart; opened: boolean }
    | { step: "connected"; result: AccountConnectPoll }
    | { step: "ended"; message: string };

const ENDED_MESSAGES: Record<string, string> = {
    denied: "The request was denied on Dograh.",
    expired: "The code expired before it was approved.",
    collected: "This code was already used.",
};

const CONNECT_PROMPT = "Connect a free Dograh account to get trial credits for Dograh models in this installation.";
const RECONNECT_PROMPT = "Dograh no longer accepts this installation's key. Reconnect your Dograh account to keep using Dograh models.";

type ConnectProps = {
    onConnected: () => Promise<void> | void;
    // Shown beside the connect button before a request starts.
    prompt?: ReactNode;
};

/** The Models page section for connecting (or reconnecting) a Dograh account. */
export function DograhAccountConnect({ onConnected, reconnect = false }: { onConnected: ConnectProps["onConnected"]; reconnect?: boolean }) {
    return <PageSection title="Dograh account"
        description="Use Dograh-managed LLM, voice and transcription models. Sign in to Dograh with your email; no keys to copy.">
        <DograhAccountConnectFlow onConnected={onConnected} prompt={reconnect ? RECONNECT_PROMPT : CONNECT_PROMPT} />
    </PageSection>;
}

/**
 * Connects a self-hosted install to a Dograh account (device authorization):
 * the person approves a code on Dograh and the install collects a service key
 * bound to their account. The key is stored server-side; the browser never
 * sees it.
 */
export function DograhAccountConnectFlow({ onConnected, prompt = CONNECT_PROMPT }: ConnectProps) {
    const [state, setState] = useState<State>({ step: "idle" });
    const [error, setError] = useState<string | null>(null);
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const expiryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const pollAbort = useRef<AbortController | null>(null);
    const mounted = useRef(true);
    // Device code of the request being polled; a poll for any other is stale.
    const active = useRef<string | null>(null);
    const onConnectedRef = useRef(onConnected);
    useEffect(() => { onConnectedRef.current = onConnected; }, [onConnected]);

    const stopPolling = () => {
        active.current = null;
        if (timer.current) clearTimeout(timer.current);
        if (expiryTimer.current) clearTimeout(expiryTimer.current);
        pollAbort.current?.abort();
        pollAbort.current = null;
        timer.current = null;
        expiryTimer.current = null;
    };
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; stopPolling(); };
    }, []);

    const poll = (request: AccountConnectStart) => {
        timer.current = setTimeout(async () => {
            pollAbort.current = new AbortController();
            const response = await pollDograhAccountConnect({
                body: { device_code: request.device_code }, signal: pollAbort.current.signal,
            });
            if (active.current !== request.device_code) return; // cancelled or restarted
            pollAbort.current = null;
            if (response.error || !response.data) {
                const status = response.response?.status;
                if (status && status >= 400 && status < 500 && status !== 429) {
                    stopPolling();
                    setState({ step: "ended", message: status === 401 || status === 403
                        ? "Your session cannot connect this installation. Sign in again, then retry."
                        : detailFromError(response.error, "Unable to continue connecting. Please start again.") });
                    return;
                }
                // The independent deadline also expires requests while offline
                // or when a poll never returns.
                poll(request);
                return;
            }
            const result = response.data;
            if (result.status === "pending") { poll(request); return; }
            stopPolling();
            if (result.status === "connected") {
                setState({ step: "connected", result });
                await onConnectedRef.current();
            } else {
                setState({ step: "ended", message: ENDED_MESSAGES[result.status] ?? "The request ended." });
            }
        }, Math.max(request.interval, 1) * 1000);
    };

    const start = async () => {
        setError(null);
        setState({ step: "starting" });
        // Open the tab inside the click so popup blockers allow it.
        const tab = window.open("about:blank", "_blank");
        const response = await startDograhAccountConnect({ body: { install_url: window.location.origin } });
        if (!mounted.current) { tab?.close(); return; }
        if (response.error || !response.data) {
            tab?.close();
            setError(detailFromError(response.error, "Unable to start connecting. Please try again."));
            setState({ step: "idle" });
            return;
        }
        const request = response.data;
        if (tab) tab.location.href = request.verification_uri_complete;
        stopPolling();
        active.current = request.device_code;
        setState({ step: "waiting", request, opened: Boolean(tab) });
        expiryTimer.current = setTimeout(() => {
            stopPolling();
            setState({ step: "ended", message: ENDED_MESSAGES.expired });
        }, Math.max(request.expires_in, 0) * 1000);
        poll(request);
    };

    const cancel = () => { stopPolling(); setState({ step: "idle" }); };

    return <div className="space-y-4 rounded-lg border p-4">
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        {(state.step === "idle" || state.step === "starting") && <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-muted-foreground">{prompt}</p>
            <Button onClick={() => void start()} disabled={state.step === "starting"}>
                {state.step === "starting" && <Loader2 className="h-4 w-4 animate-spin" />}Connect Dograh account
            </Button>
        </div>}
        {state.step === "waiting" && <div className="space-y-3">
            <p className="text-sm">{state.opened
                ? "Approve this code on Dograh. We opened the approval page in a new tab."
                : "Open the approval page and approve this code on Dograh."}</p>
            <p className="font-mono text-2xl tracking-widest">{state.request.user_code}</p>
            <div className="flex flex-wrap items-center gap-3">
                <Button asChild variant="outline">
                    <a href={state.request.verification_uri_complete} target="_blank" rel="noopener noreferrer">Open approval page<ExternalLink className="h-4 w-4" /></a>
                </Button>
                <Button variant="ghost" onClick={cancel}>Cancel</Button>
            </div>
            <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Waiting for approval…</p>
        </div>}
        {state.step === "connected" && <div role="status" className="space-y-1">
            <p className="font-medium">Connected{state.result.team_name ? ` to ${state.result.team_name}` : ""}.</p>
            <p className="text-sm text-muted-foreground">{state.result.made_default
                ? "Dograh models are now this installation's default model configuration."
                : "Your Dograh connection now uses this account. Select it in a model configuration to use it."}</p>
        </div>}
        {state.step === "ended" && <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm">{state.message}</p>
            <Button variant="outline" onClick={() => void start()}>Start again</Button>
        </div>}
    </div>;
}
