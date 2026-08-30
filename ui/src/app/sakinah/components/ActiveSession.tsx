import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useWebSocketRTC } from "@/app/workflow/[workflowId]/run/[runId]/hooks";
import { endSessionApiV1SakinahSessionsSessionIdEndPost } from "@/client";

import { SessionStatus } from "./SessionStatus";
import { TranscriptPanel } from "./TranscriptPanel";
import type { SakinahSession, TranscriptTurn } from "./types";

interface ActiveSessionProps {
    session: SakinahSession;
    accessToken: string;
    onSaved: (sessionId: string) => void;
}

export function ActiveSession({ session, accessToken, onSaved }: ActiveSessionProps) {
    const [ending, setEnding] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);
    const startedRef = useRef(false);
    const savedRef = useRef(false);
    const {
        audioRef,
        permissionError,
        isCompleted,
        connectionStatus,
        start,
        stop,
        isStarting,
        feedbackMessages,
    } = useWebSocketRTC({
        workflowId: session.workflow_id,
        workflowRunId: session.workflow_run_id,
        accessToken,
        initialContextVariables: {
            scenario: session.scenario,
            session_id: session.session_id,
        },
    });

    const turns = useMemo<TranscriptTurn[]>(
        () =>
            feedbackMessages.flatMap((message) => {
                if (message.type !== "user-transcription" && message.type !== "bot-text") {
                    return [];
                }
                return [{
                    role: message.type === "user-transcription" ? "user" as const : "sakinah" as const,
                    text: message.text,
                    final: message.final ?? false,
                    timestamp: message.timestamp,
                }];
            }),
        [feedbackMessages],
    );
    const pipelineError = [...feedbackMessages].reverse().find(
        (message) => message.type === "pipeline-error",
    )?.text ?? null;

    const saveSession = useCallback(async () => {
        if (savedRef.current) return;
        savedRef.current = true;
        setEnding(true);
        setSaveError(null);
        const response = await endSessionApiV1SakinahSessionsSessionIdEndPost({
            path: { session_id: session.session_id },
            body: {
                turns,
                ended_at: new Date().toISOString(),
                timings: {
                    duration_ms: Math.max(0, Date.now() - new Date(session.started_at).getTime()),
                },
            },
        });
        if (response.error) {
            savedRef.current = false;
            setEnding(false);
            setSaveError("The session ended, but its transcript could not be saved.");
            return;
        }
        setEnding(false);
        onSaved(session.session_id);
    }, [onSaved, session.session_id, session.started_at, turns]);

    useEffect(() => {
        if (startedRef.current) return;
        startedRef.current = true;
        void start();
    }, [start]);

    useEffect(() => {
        if (isCompleted) void saveSession();
    }, [isCompleted, saveSession]);

    const handleEnd = () => {
        stop();
        void saveSession();
    };

    return (
        <div className="space-y-4 lg:col-span-2">
            <SessionStatus
                connectionStatus={connectionStatus}
                isStarting={isStarting}
                ending={ending}
                error={permissionError ?? pipelineError ?? saveError}
                savedSessionId={null}
                onEnd={handleEnd}
            />
            <TranscriptPanel turns={turns} />
            <audio ref={audioRef} autoPlay playsInline className="hidden" />
        </div>
    );
}
