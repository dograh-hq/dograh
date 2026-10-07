"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    endTextChatSessionApiV1WorkflowWorkflowIdTextChatSessionsRunIdEndPost,
    getTextChatSessionApiV1WorkflowWorkflowIdTextChatSessionsRunIdGet,
    recoverTextChatSession,
    rewindTextChatSessionApiV1WorkflowWorkflowIdTextChatSessionsRunIdRewindPost,
    streamTextChatMessage,
    streamTextChatSession,
} from "@/client/sdk.gen";
import { conversationItemsFromTextChatTurns } from "@/components/workflow/conversation/adapters/fromTextChatTurns";

import { applyTextChatStreamEvent, reconcileTextChatSession } from "./textChatStream";
import {
    EMPTY_TEXT_CHAT_TURNS,
    type TextChatSession,
    type TextChatTurn,
    toTextChatSession,
    type TurnActionState,
    type WorkflowRuntimeNodeTransition,
} from "./types";
import { extractSdkErrorMessage, getErrorMessage, getReplayCursorTurnId } from "./utils";

type StreamOptions = {
    fetch: typeof fetch;
    signal: AbortSignal;
    sseMaxRetryAttempts: number;
    onSseError: (error: unknown) => void;
};

type OpenChatStream = (options: StreamOptions) => ReturnType<typeof streamTextChatMessage>;

interface UseTextChatSessionProps {
    workflowId: number;
    ready: boolean;
    initialContextVariables?: Record<string, string>;
    disabled: boolean;
    onActiveChange?: (active: boolean) => void;
    onNodeTransition?: (transition: WorkflowRuntimeNodeTransition) => void;
}

export function useTextChatSession({
    workflowId,
    ready,
    initialContextVariables,
    disabled,
    onActiveChange,
    onNodeTransition,
}: UseTextChatSessionProps) {
    const [session, setSession] = useState<TextChatSession | null>(null);
    const [started, setStarted] = useState(false);
    const [draft, setDraft] = useState("");
    const [creatingSession, setCreatingSession] = useState(false);
    const [sendingMessage, setSendingMessage] = useState(false);
    const [endingSession, setEndingSession] = useState(false);
    const [editingTurnId, setEditingTurnId] = useState<string | null>(null);
    const [activeTurnAction, setActiveTurnAction] = useState<TurnActionState | null>(null);
    const lastNotifiedNodeTransitionIdRef = useRef<string | null>(null);
    const activeRequest = useRef<AbortController | null>(null);
    const latestSession = useRef<TextChatSession | null>(null);
    const acceptedRunId = useRef<number | null>(null);
    const [recoveringRunId, setRecoveringRunId] = useState<number | null>(null);
    const [recoveringRequestId, setRecoveringRequestId] = useState<string | null>(null);
    const streamResponseStatus = useRef<number | null>(null);
    const recovering = recoveringRunId !== null || recoveringRequestId !== null;
    const pendingTurn = session?.session_data.status === "pending_assistant_turn";
    const sessionRunId = session?.workflow_run_id;

    const updateSession = useCallback((next: TextChatSession | null) => {
        latestSession.current = next;
        setSession(next);
    }, []);

    useEffect(() => () => { activeRequest.current?.abort(); }, []);

    const refreshSession = useCallback(async (runId: number, signal: AbortSignal) => {
        const response = await getTextChatSessionApiV1WorkflowWorkflowIdTextChatSessionsRunIdGet({
            path: { workflow_id: workflowId, run_id: runId },
            signal,
        });
        if (signal.aborted) return;
        if (response.error || !response.data) {
            throw new Error(extractSdkErrorMessage(response.error, "Failed to refresh chat"));
        }
        const refreshed = toTextChatSession(response.data);
        updateSession(reconcileTextChatSession(latestSession.current, refreshed));
        if (refreshed.session_data.status !== "pending_assistant_turn") setRecoveringRunId(null);
    }, [updateSession, workflowId]);

    const consumeStream = useCallback(async (open: OpenChatStream, controller: AbortController) => {
        let streamError: unknown;
        streamResponseStatus.current = null;
        const { stream } = await open({
            // The generated SDK still builds/authenticates this request. Capture
            // its response header before the body can fail or end without data.
            fetch: async (input, init) => {
                const response = await fetch(input, init);
                streamResponseStatus.current = response.status;
                const runId = Number(response.headers.get("X-Workflow-Run-Id"));
                if (response.ok && Number.isSafeInteger(runId) && runId > 0) acceptedRunId.current = runId;
                return response;
            },
            signal: controller.signal,
            // Reconnecting a POST would execute the message's tools again.
            sseMaxRetryAttempts: 1,
            onSseError: error => { streamError = error; },
        });
        let completed = false;
        for await (const update of stream) {
            if (controller.signal.aborted) return;
            if (update.type === "error") throw new Error(update.message);
            updateSession(applyTextChatStreamEvent(latestSession.current, update));
            if (update.type === "session") {
                setDraft("");
                setEditingTurnId(null);
            }
            if (update.type === "complete") completed = true;
        }
        if (!completed && !controller.signal.aborted) {
            throw streamError ?? new Error("Chat connection interrupted");
        }
    }, [updateSession]);

    const recoverStream = useCallback(async (error: unknown, controller: AbortController, requestId?: string) => {
        if (controller.signal.aborted) return;
        toast.error(getErrorMessage(error));
        const runId = latestSession.current?.workflow_run_id ?? acceptedRunId.current;
        if (runId !== null) {
            setRecoveringRunId(runId);
            try {
                await refreshSession(runId, controller.signal);
            } catch {
                // A pending turn is polled below until its persisted result is available.
            }
        } else if (requestId && (streamResponseStatus.current === null || streamResponseStatus.current < 400 || streamResponseStatus.current >= 500)) {
            // No response does not mean the POST was rejected. Look up the
            // client correlation ID across workers, even if creation is still
            // being prepared. Only reads may be retried after an ambiguous POST.
            setRecoveringRequestId(requestId);
        } else {
            setStarted(false);
        }
    }, [refreshSession]);

    // Once a stream disconnects, recover by reading the accepted turn. Never
    // resubmit it: an HTTP tool may already have performed its side effects.
    useEffect(() => {
        const runId = recoveringRunId ?? sessionRunId;
        if ((!pendingTurn && !recovering) || sendingMessage || creatingSession || !ready) return;
        const controller = new AbortController();
        let timer: ReturnType<typeof setTimeout>;
        const poll = async () => {
            try {
                if (runId !== undefined) {
                    await refreshSession(runId, controller.signal);
                } else if (recoveringRequestId) {
                    const response = await recoverTextChatSession({
                        path: { workflow_id: workflowId, request_id: recoveringRequestId },
                        signal: controller.signal,
                    });
                    if (!controller.signal.aborted && !response.error && response.data) {
                        updateSession(toTextChatSession(response.data));
                        setRecoveringRequestId(null);
                    }
                }
            } catch {
                // Retry only this read when connectivity returns.
            }
            if (!controller.signal.aborted) timer = setTimeout(poll, 1000);
        };
        void poll();
        return () => { controller.abort(); clearTimeout(timer); };
    }, [creatingSession, pendingTurn, ready, recovering, recoveringRequestId, recoveringRunId, refreshSession, sendingMessage, sessionRunId, updateSession, workflowId]);

    const turns = session?.session_data.turns ?? EMPTY_TEXT_CHAT_TURNS;
    const editingTurn = editingTurnId
        ? turns.find((turn) => turn.id === editingTurnId) ?? null
        : null;
    const composerId = `workflow-tester-compose-${workflowId}`;
    const conversationItems = conversationItemsFromTextChatTurns(turns);

    const createSession = useCallback(async () => {
        if (disabled || !ready || activeRequest.current) return;
        const controller = new AbortController();
        activeRequest.current = controller;
        setCreatingSession(true);
        const requestId = crypto.randomUUID();
        try {
            await consumeStream(options => streamTextChatSession({
                ...options,
                path: { workflow_id: workflowId },
                body: {
                    request_id: requestId,
                    initial_context: initialContextVariables ?? {},
                    annotations: {
                        tester: { source: "workflow_editor", modality: "text", ui_mode: "manual_text" },
                    },
                },
            }), controller);
            if (!controller.signal.aborted) setDraft("");
        } catch (error) {
            await recoverStream(error, controller, requestId);
        } finally {
            if (activeRequest.current === controller) activeRequest.current = null;
            if (!controller.signal.aborted) setCreatingSession(false);
        }
    }, [consumeStream, disabled, initialContextVariables, ready, recoverStream, workflowId]);

    useEffect(() => {
        if (!started || creatingSession || recovering || session || !ready || disabled) {
            return;
        }
        void createSession();
    }, [createSession, creatingSession, disabled, ready, recovering, session, started]);

    useEffect(() => {
        onActiveChange?.(started);
    }, [onActiveChange, started]);

    useEffect(() => {
        const latestNodeTransition = [...conversationItems]
            .reverse()
            .find(
                (item): item is WorkflowRuntimeNodeTransition =>
                    item.kind === "node-transition" && !!item.nodeId,
            );

        if (!latestNodeTransition?.nodeId) {
            return;
        }

        if (lastNotifiedNodeTransitionIdRef.current === latestNodeTransition.id) {
            return;
        }

        lastNotifiedNodeTransitionIdRef.current = latestNodeTransition.id;
        onNodeTransition?.(latestNodeTransition);
    }, [conversationItems, onNodeTransition]);

    useEffect(() => {
        if (!editingTurnId) {
            return;
        }
        if (!turns.some((turn) => turn.id === editingTurnId)) {
            setEditingTurnId(null);
            setDraft("");
        }
    }, [editingTurnId, turns]);

    const submitMessage = useCallback(async (messageText: string, replayOptions?: TurnActionState) => {
        const trimmedText = messageText.trim();
        if (!session || session.is_completed || !trimmedText || !ready || disabled ||
            endingSession || pendingTurn || recovering || activeRequest.current) return;
        const controller = new AbortController();
        activeRequest.current = controller;

        setSendingMessage(true);
        if (replayOptions) {
            setActiveTurnAction(replayOptions);
        }

        try {
            let activeSession = session;

            if (replayOptions) {
                const rewindResponse = await rewindTextChatSessionApiV1WorkflowWorkflowIdTextChatSessionsRunIdRewindPost({
                    path: { workflow_id: workflowId, run_id: activeSession.workflow_run_id },
                    signal: controller.signal,
                    body: {
                        cursor_turn_id: getReplayCursorTurnId(activeSession.session_data.turns, replayOptions.turnId),
                        expected_revision: activeSession.revision,
                    },
                });

                if (rewindResponse.error || !rewindResponse.data) {
                    throw new Error(extractSdkErrorMessage(rewindResponse.error, "Failed to rewind session"));
                }

                activeSession = toTextChatSession(rewindResponse.data);
                updateSession(activeSession);
            }

            await consumeStream(options => streamTextChatMessage({
                ...options,
                path: { workflow_id: workflowId, run_id: activeSession.workflow_run_id },
                body: { text: trimmedText, expected_revision: activeSession.revision },
            }), controller);
            if (!controller.signal.aborted) {
                setDraft("");
                setEditingTurnId(null);
            }
        } catch (error) {
            await recoverStream(error, controller);
        } finally {
            if (activeRequest.current === controller) activeRequest.current = null;
            if (!controller.signal.aborted) {
                setSendingMessage(false);
                setActiveTurnAction(null);
            }
        }
    }, [consumeStream, disabled, endingSession, pendingTurn, ready, recovering, recoverStream, session, updateSession, workflowId]);

    const endSession = useCallback(async () => {
        if (!session || session.is_completed || pendingTurn || recovering || activeRequest.current || endingSession) return;

        setEndingSession(true);
        try {
            const response = await endTextChatSessionApiV1WorkflowWorkflowIdTextChatSessionsRunIdEndPost({
                path: { workflow_id: workflowId, run_id: session.workflow_run_id },
                body: { expected_revision: session.revision },
            });

            if (response.error || !response.data) {
                throw new Error(extractSdkErrorMessage(response.error, "Failed to end chat session"));
            }

            updateSession(toTextChatSession(response.data));
            setDraft("");
            setEditingTurnId(null);
            toast.success("Chat ended");
        } catch (error) {
            toast.error(getErrorMessage(error));
        } finally {
            setEndingSession(false);
        }
    }, [endingSession, pendingTurn, recovering, session, updateSession, workflowId]);

    const rewindTurn = useCallback(async (turn: TextChatTurn) => {
        if (!turn.user_message) return;
        await submitMessage(turn.user_message.text, { turnId: turn.id, type: "rewind" });
    }, [submitMessage]);

    const startEditingTurn = useCallback((turn: TextChatTurn) => {
        if (!turn.user_message) return;
        const nextText = turn.user_message.text;

        setEditingTurnId(turn.id);
        setDraft(nextText);

        requestAnimationFrame(() => {
            const textarea = document.getElementById(composerId) as HTMLTextAreaElement | null;
            textarea?.focus();
            textarea?.setSelectionRange(nextText.length, nextText.length);
        });
    }, [composerId]);

    const cancelEditingTurn = useCallback(() => {
        setEditingTurnId(null);
        setDraft("");
    }, []);

    const submitComposer = useCallback(async () => {
        if (editingTurnId) {
            await submitMessage(draft, { turnId: editingTurnId, type: "edit" });
            return;
        }
        await submitMessage(draft);
    }, [draft, editingTurnId, submitMessage]);

    return {
        session,
        started,
        draft,
        turns,
        editingTurn,
        editingTurnId,
        creatingSession: creatingSession || (recovering && !session),
        sendingMessage: sendingMessage || Boolean(pendingTurn) || recovering,
        endingSession,
        activeTurnAction,
        composerId,
        inputDisabled: disabled || !ready || !session || session.is_completed || endingSession || Boolean(pendingTurn) || sendingMessage || recovering,
        conversationItems,
        setDraft,
        startSession: () => setStarted(true),
        rewindTurn,
        startEditingTurn,
        cancelEditingTurn,
        submitComposer,
        endSession,
    };
}
