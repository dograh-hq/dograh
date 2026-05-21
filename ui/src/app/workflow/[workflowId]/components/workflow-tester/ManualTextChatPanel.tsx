"use client";

import { Skeleton } from "@/components/ui/skeleton";
import type { ConversationItem } from "@/components/workflow/conversation";
import { ConversationTimeline } from "@/components/workflow/conversation";

import { ChatComposer } from "./ChatComposer";
import { DisabledNotice, ManualChatEmptyState, TypingIndicator } from "./shared";
import { TurnMessageActions } from "./TurnMessageActions";
import type { WorkflowRuntimeNodeTransition } from "./types";
import { useTextChatSession } from "./useTextChatSession";

interface ManualTextChatPanelProps {
    workflowId: number;
    ready: boolean;
    initialContextVariables?: Record<string, string>;
    disabled: boolean;
    disabledReason: string | null;
    onActiveChange?: (active: boolean) => void;
    onNodeTransition?: (transition: WorkflowRuntimeNodeTransition) => void;
}

export function ManualTextChatPanel({
    workflowId,
    ready,
    initialContextVariables,
    disabled,
    disabledReason,
    onActiveChange,
    onNodeTransition,
}: ManualTextChatPanelProps) {
    const {
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
        inputDisabled,
        conversationItems,
        setDraft,
        startSession,
        rewindTurn,
        startEditingTurn,
        cancelEditingTurn,
        submitComposer,
    } = useTextChatSession({
        workflowId,
        ready,
        initialContextVariables,
        disabled,
        onActiveChange,
        onNodeTransition,
    });

    if (!started && !session) {
        return (
            <div className="flex h-full min-h-0 flex-col gap-3">
                {disabledReason ? <DisabledNotice reason={disabledReason} /> : null}
                <ManualChatEmptyState disabled={disabled} ready={ready} onStart={startSession} />
            </div>
        );
    }

    return (
        <div className="flex min-h-0 flex-1 flex-col">
            {disabledReason ? (
                <div className="pb-3">
                    <DisabledNotice reason={disabledReason} />
                </div>
            ) : null}

            <div className="flex min-h-0 flex-1 flex-col">
                {creatingSession && !session ? (
                    <div className="space-y-3 py-1">
                        <Skeleton className="ml-auto h-9 w-2/3 rounded-2xl" />
                        <Skeleton className="h-12 w-3/4 rounded-2xl" />
                    </div>
                ) : turns.length === 0 ? (
                    <div className="flex h-full items-center justify-center px-4 py-10 text-center">
                        <p className="text-sm text-muted-foreground">
                            {disabled
                                ? (disabledReason ?? "Testing is paused.")
                                : "Send a message to start the conversation."}
                        </p>
                    </div>
                ) : (
                    <ConversationTimeline
                        items={conversationItems}
                        autoScroll={true}
                        scrollBehavior="smooth"
                        emptyState={{
                            title: "No conversation recorded",
                            subtitle: "Send a message to start the conversation.",
                        }}
                        pendingIndicator={sendingMessage ? <TypingIndicator /> : null}
                        className="py-1"
                        renderItemActions={(item: ConversationItem) => {
                            if (item.kind !== "message" || item.role !== "user" || !item.turnId) {
                                return null;
                            }

                            const turn = turns.find((candidate) => candidate.id === item.turnId);
                            if (!turn?.user_message) {
                                return null;
                            }

                            const rewindingThisTurn =
                                activeTurnAction?.turnId === turn.id && activeTurnAction.type === "rewind";
                            const rerunningEditedTurn =
                                activeTurnAction?.turnId === turn.id && activeTurnAction.type === "edit";

                            return (
                                <TurnMessageActions
                                    disabled={disabled || sendingMessage}
                                    editing={editingTurnId === turn.id}
                                    rewinding={rewindingThisTurn}
                                    rerunningEdit={rerunningEditedTurn}
                                    onRewind={() => void rewindTurn(turn)}
                                    onEdit={() => startEditingTurn(turn)}
                                />
                            );
                        }}
                    />
                )}
            </div>

            <ChatComposer
                composerId={composerId}
                draft={draft}
                ready={ready}
                editing={!!editingTurn}
                sendingMessage={sendingMessage}
                inputDisabled={inputDisabled}
                onDraftChange={setDraft}
                onCancelEditing={cancelEditingTurn}
                onSubmit={submitComposer}
            />
        </div>
    );
}
