import { useEffect, useRef, useState } from 'react';

import { cancelBuilderRunApiV1WorkflowBuilderSessionIdRunsRunIdCancelPost, getBuilderRunEventsApiV1WorkflowBuilderSessionIdRunsRunIdEventsGet, sendBuilderTurnApiV1WorkflowBuilderSessionIdTurnPost } from '@/client/sdk.gen';
import type { BuilderSession, BuilderTurnRequest } from '@/client/types.gen';
import { detailFromError } from '@/lib/apiError';

type Callbacks = {
    getAccessToken: () => Promise<string | null>;
    onSession: (session: BuilderSession) => void;
    onStatus: (status: string) => void;
    onError: (error: string | null) => void;
    onSaved?: () => Promise<void>;
};
type Run = { id: string; sessionId: string; after: number; request?: BuilderTurnRequest };

/** The connection is disposable; a run belongs to the worker until it finishes or is stopped. */
export function useBuilderExecution(callbacks: Callbacks) {
    const latest = useRef(callbacks); latest.current = callbacks;
    const current = useRef<Run | null>(null);
    const connection = useRef<AbortController | null>(null);
    const [busy, setBusy] = useState(false);
    const [disconnected, setDisconnected] = useState(false);
    const [stopping, setStopping] = useState(false);
    const detach = () => { connection.current?.abort(); current.current = null; setBusy(false); setDisconnected(false); };
    useEffect(() => () => { connection.current?.abort(); }, []);

    const watch = async (run: Run) => {
        connection.current?.abort();
        const controller = new AbortController(); connection.current = controller;
        current.current = run; setBusy(true); setDisconnected(false); setStopping(false);
        latest.current.onError(null);
        let terminal = false;
        let httpFailure: { status: number; message: string } | undefined;
        try {
            const token = await latest.current.getAccessToken();
            const options = {
                signal: controller.signal, headers: { Authorization: `Bearer ${token}` }, sseMaxRetryAttempts: 1,
                fetch: async (request: RequestInfo | URL, init?: RequestInit) => {
                    const response = await fetch(request, init);
                    if (!response.ok) {
                        const body = await response.clone().json().catch(() => null);
                        httpFailure = { status: response.status, message: detailFromError(body, `Could not connect to the builder (HTTP ${response.status}).`) };
                    }
                    return response;
                },
            };
            // Retrying an admission uses the same request ID and body. The server
            // replays the existing run, including when the first response was lost.
            const result = run.request
                ? await sendBuilderTurnApiV1WorkflowBuilderSessionIdTurnPost({ path: { session_id: run.sessionId }, body: run.request, ...options })
                : await getBuilderRunEventsApiV1WorkflowBuilderSessionIdRunsRunIdEventsGet({ path: { session_id: run.sessionId, run_id: run.id }, query: { after: run.after }, ...options });
            for await (const raw of result.stream) {
                if (controller.signal.aborted) return;
                if (!raw || typeof raw !== 'object') continue;
                const event = raw as { type: string; seq?: number; run_id?: string; message?: string; session?: BuilderSession };
                if (event.seq != null && event.seq <= run.after) continue;
                if (event.type === 'run') run.request = undefined; // Admission is confirmed; reconnect using GET.
                if (event.type === 'session' && event.session) latest.current.onSession(event.session);
                if (event.type === 'status') latest.current.onStatus(event.message ?? 'Working…');
                if (event.type === 'saved') await latest.current.onSaved?.();
                // Advance after callbacks, so a failed canvas refresh is replayable.
                if (event.seq != null) run.after = event.seq;
                if (event.type === 'error') { terminal = true; throw new Error(event.message ?? 'Builder failed.'); }
                if (event.type === 'done' || event.type === 'cancelled') { terminal = true; break; }
            }
            if (httpFailure) {
                terminal = [400, 401, 403, 404, 409, 422].includes(httpFailure.status);
                throw new Error(httpFailure.message);
            }
            if (!terminal) throw new Error('Connection interrupted. Your agent is still being built. Reconnect to see progress.');
        } catch (err) {
            if (!controller.signal.aborted) {
                latest.current.onError(err instanceof Error ? err.message : 'Could not connect to the builder.');
                if (!terminal) setDisconnected(true);
            }
        } finally {
            if (!controller.signal.aborted && terminal) {
                current.current = null; setBusy(false); setStopping(false); setDisconnected(false); latest.current.onStatus('');
            }
        }
    };
    const start = (sessionId: string, request: BuilderTurnRequest) => watch({ id: request.request_id, sessionId, after: 0, request });
    const restore = (sessionId: string, runId: string) => watch({ id: runId, sessionId, after: 0 });
    const reconnect = () => { if (current.current) void watch(current.current); };
    const stop = async () => {
        const run = current.current;
        if (!run) return;
        setStopping(true);
        try {
            const token = await latest.current.getAccessToken();
            const result = await cancelBuilderRunApiV1WorkflowBuilderSessionIdRunsRunIdCancelPost({ path: { session_id: run.sessionId, run_id: run.id }, headers: { Authorization: `Bearer ${token}` } });
            if (result.error) throw new Error(detailFromError(result.error, 'Could not stop the builder.'));
            latest.current.onStatus('Stopping…');
            if (disconnected) reconnect();
        } catch (err) { setStopping(false); latest.current.onError(err instanceof Error ? err.message : 'Could not stop the builder.'); }
    };
    return { busy, disconnected, stopping, start, restore, reconnect, stop, detach };
}
