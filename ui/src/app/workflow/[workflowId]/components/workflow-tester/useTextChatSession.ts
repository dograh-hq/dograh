"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    endTextChatSessionApiV1WorkflowWorkflowIdTextChatSessionsRunIdEndPost,
    getTextChatSessionApiV1WorkflowWorkflowIdTextChatSessionsRunIdGet,
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
        updateSession(reconcileTextChatSession(latestSession.current, toTextChatSession(response.data)));
    }, [updateSession, workflowId]);

    const consumeStream = useCallback(async (open: OpenChatStream, controller: AbortController) => {
        let streamError: unknown;
        const { stream } = await open({
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

    const recoverStream = useCallback(async (error: unknown, controller: AbortController) => {
        if (controller.signal.aborted) return;
        toast.error(getErrorMessage(error));
        const current = latestSession.current;
        if (current) {
            try {
                await refreshSession(current.workflow_run_id, controller.signal);
            } catch {
                // A pending turn is polled below until its persisted result is available.
            }
        } else {
            setStarted(false);
        }
    }, [refreshSession]);

    // Once a stream disconnects, recover by reading the accepted turn. Never
    // resubmit it: an HTTP tool may already have performed its side effects.
    useEffect(() => {
        if (!pendingTurn || sendingMessage || creatingSession || sessionRunId === undefined || !ready) return;
        const controller = new AbortController();
        let timer: ReturnType<typeof setTimeout>;
        const poll = async () => {
            try {
                await refreshSession(sessionRunId, controller.signal);
            } catch {
                // Retry only this read when connectivity returns.
            }
            if (!controller.signal.aborted) timer = setTimeout(poll, 1000);
        };
        void poll();
        return () => { controller.abort(); clearTimeout(timer); };
    }, [creatingSession, pendingTurn, ready, refreshSession, sendingMessage, sessionRunId]);

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
        try {
            await consumeStream(options => streamTextChatSession({
                ...options,
                path: { workflow_id: workflowId },
                body: {
                    initial_context: initialContextVariables ?? {},
                    annotations: {
                        tester: { source: "workflow_editor", modality: "text", ui_mode: "manual_text" },
                    },
                },
            }), controller);
            if (!controller.signal.aborted) setDraft("");
        } catch (error) {
            await recoverStream(error, controller);
        } finally {
            if (activeRequest.current === controller) activeRequest.current = null;
            if (!controller.signal.aborted) setCreatingSession(false);
        }
    }, [consumeStream, disabled, initialContextVariables, ready, recoverStream, workflowId]);

    useEffect(() => {
        if (!started || creatingSession || session || !ready || disabled) {
            return;
        }
        void createSession();
    }, [createSession, creatingSession, disabled, ready, session, started]);

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
            endingSession || pendingTurn || activeRequest.current) return;
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
    }, [consumeStream, disabled, endingSession, pendingTurn, ready, recoverStream, session, updateSession, workflowId]);

    const endSession = useCallback(async () => {
        if (!session || session.is_completed || pendingTurn || activeRequest.current || endingSession) return;

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
    }, [endingSession, pendingTurn, session, updateSession, workflowId]);

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
        creatingSession,
        sendingMessage: sendingMessage || Boolean(pendingTurn),
        endingSession,
        activeTurnAction,
        composerId,
        inputDisabled: disabled || !ready || !session || session.is_completed || endingSession || Boolean(pendingTurn) || sendingMessage,
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
