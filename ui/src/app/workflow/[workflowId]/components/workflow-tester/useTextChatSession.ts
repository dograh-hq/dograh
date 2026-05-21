"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    appendTextChatMessageApiV1WorkflowWorkflowIdTextChatSessionsRunIdMessagesPost,
    createTextChatSessionApiV1WorkflowWorkflowIdTextChatSessionsPost,
    rewindTextChatSessionApiV1WorkflowWorkflowIdTextChatSessionsRunIdRewindPost,
} from "@/client/sdk.gen";
import { conversationItemsFromTextChatTurns } from "@/components/workflow/conversation/adapters/fromTextChatTurns";

import {
    EMPTY_TEXT_CHAT_TURNS,
    type TextChatSession,
    type TextChatTurn,
    toTextChatSession,
    type TurnActionState,
    type WorkflowRuntimeNodeTransition,
} from "./types";
import { extractSdkErrorMessage, getErrorMessage, getReplayCursorTurnId } from "./utils";

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
    const [editingTurnId, setEditingTurnId] = useState<string | null>(null);
    const [activeTurnAction, setActiveTurnAction] = useState<TurnActionState | null>(null);
    const lastNotifiedNodeTransitionIdRef = useRef<string | null>(null);

    const turns = session?.session_data.turns ?? EMPTY_TEXT_CHAT_TURNS;
    const editingTurn = editingTurnId
        ? turns.find((turn) => turn.id === editingTurnId) ?? null
        : null;
    const composerId = `workflow-tester-compose-${workflowId}`;
    const conversationItems = conversationItemsFromTextChatTurns(turns);

    const createSession = useCallback(async () => {
        if (disabled) return;
        setCreatingSession(true);
        try {
            const response = await createTextChatSessionApiV1WorkflowWorkflowIdTextChatSessionsPost({
                path: { workflow_id: workflowId },
                body: {
                    initial_context: initialContextVariables ?? {},
                    annotations: {
                        tester: {
                            source: "workflow_editor",
                            modality: "text",
                            ui_mode: "manual_text",
                        },
                    },
                },
            });

            if (response.error || !response.data) {
                throw new Error(extractSdkErrorMessage(response.error, "Failed to create chat session"));
            }

            setSession(toTextChatSession(response.data));
            setDraft("");
        } catch (error) {
            setSession(null);
            setStarted(false);
            toast.error(getErrorMessage(error));
        } finally {
            setCreatingSession(false);
        }
    }, [disabled, initialContextVariables, workflowId]);

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
        if (!session || !trimmedText || disabled) return;

        setSendingMessage(true);
        if (replayOptions) {
            setActiveTurnAction(replayOptions);
        }

        try {
            let activeSession = session;

            if (replayOptions) {
                const rewindResponse = await rewindTextChatSessionApiV1WorkflowWorkflowIdTextChatSessionsRunIdRewindPost({
                    path: { workflow_id: workflowId, run_id: activeSession.workflow_run_id },
                    body: {
                        cursor_turn_id: getReplayCursorTurnId(activeSession.session_data.turns, replayOptions.turnId),
                        expected_revision: activeSession.revision,
                    },
                });

                if (rewindResponse.error || !rewindResponse.data) {
                    throw new Error(extractSdkErrorMessage(rewindResponse.error, "Failed to rewind session"));
                }

                activeSession = toTextChatSession(rewindResponse.data);
                setSession(activeSession);
            }

            const response = await appendTextChatMessageApiV1WorkflowWorkflowIdTextChatSessionsRunIdMessagesPost({
                path: { workflow_id: workflowId, run_id: activeSession.workflow_run_id },
                body: {
                    text: trimmedText,
                    expected_revision: activeSession.revision,
                },
            });

            if (response.error || !response.data) {
                throw new Error(extractSdkErrorMessage(response.error, "Failed to send message"));
            }

            setSession(toTextChatSession(response.data));
            setDraft("");
            setEditingTurnId(null);
        } catch (error) {
            toast.error(getErrorMessage(error));
        } finally {
            setSendingMessage(false);
            setActiveTurnAction(null);
        }
    }, [disabled, session, workflowId]);

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
        sendingMessage,
        activeTurnAction,
        composerId,
        inputDisabled: disabled || !session,
        conversationItems,
        setDraft,
        startSession: () => setStarted(true),
        rewindTurn,
        startEditingTurn,
        cancelEditingTurn,
        submitComposer,
    };
}
