"use client";

import { AlertCircle, Loader2, MessageSquareText, Mic, Pencil, Phone, RefreshCw, RotateCcw, Sparkles, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    appendTextChatMessageApiV1WorkflowWorkflowIdTextChatSessionsRunIdMessagesPost,
    createTextChatSessionApiV1WorkflowWorkflowIdTextChatSessionsPost,
    createWorkflowRunApiV1WorkflowWorkflowIdRunsPost,
    rewindTextChatSessionApiV1WorkflowWorkflowIdTextChatSessionsRunIdRewindPost,
} from "@/client/sdk.gen";
import type { WorkflowRunTextSessionResponse } from "@/client/types.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { WORKFLOW_RUN_MODES } from "@/constants/workflowRunModes";
import { useAuth } from "@/lib/auth";
import { cn, getRandomId } from "@/lib/utils";

import { ApiKeyErrorDialog, ConnectionStatus, RealtimeFeedback, WorkflowConfigErrorDialog } from "../run/[runId]/components";
import { useWebSocketRTC } from "../run/[runId]/hooks";

interface WorkflowTesterPanelProps {
    workflowId: number;
    initialContextVariables?: Record<string, string>;
    disabled: boolean;
    disabledReason: string | null;
    className?: string;
    onClose?: () => void;
}

interface TextChatMessage {
    text: string;
    created_at: string;
}

interface TextChatTurn {
    id: string;
    status: string;
    created_at: string;
    user_message: TextChatMessage | null;
    assistant_message: TextChatMessage | null;
    events: Array<Record<string, unknown>>;
    usage: Record<string, unknown>;
}

interface TextChatSessionData {
    version: number;
    status: string;
    cursor_turn_id: string | null;
    turns: TextChatTurn[];
    discarded_future: Array<Record<string, unknown>>;
    simulator: {
        enabled: boolean;
        config: Record<string, unknown>;
    };
}

interface TextChatCheckpoint {
    version: number;
    anchor_turn_id: string | null;
    current_node_id: string | null;
    messages: Array<Record<string, unknown>>;
    gathered_context: Record<string, unknown>;
    tool_state: Record<string, unknown>;
}

type TextChatSession = Omit<WorkflowRunTextSessionResponse, "session_data" | "checkpoint"> & {
    session_data: TextChatSessionData;
    checkpoint: TextChatCheckpoint;
};

interface TextChatToolEvent {
    kind: "start" | "result";
    functionName: string;
    resultText?: string;
}

interface TurnActionState {
    turnId: string;
    type: "rewind" | "edit";
}

const EMPTY_TEXT_CHAT_TURNS: TextChatTurn[] = [];

function toTextChatSession(response: WorkflowRunTextSessionResponse): TextChatSession {
    return {
        ...response,
        session_data: response.session_data as unknown as TextChatSessionData,
        checkpoint: response.checkpoint as unknown as TextChatCheckpoint,
    };
}

function getErrorMessage(error: unknown) {
    if (error instanceof Error) return error.message;
    return "Something went wrong";
}

function extractSdkErrorMessage(error: unknown, fallback: string) {
    if (!error) return fallback;
    if (typeof error === "string") return error;
    if (typeof error === "object") {
        const detail = (error as { detail?: unknown }).detail;
        if (typeof detail === "string") return detail;
        if (detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string") {
            return (detail as { message: string }).message;
        }
    }
    return fallback;
}

function DisabledNotice({ reason }: { reason: string }) {
    return (
        <div className="rounded-lg border border-amber-200/80 bg-amber-50/80 px-3 py-2.5 text-sm text-amber-900 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200">
            <div className="flex items-start gap-3">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                <div className="space-y-0.5">
                    <p className="font-medium">Testing is paused</p>
                    <p className="text-amber-800/90 dark:text-amber-300">{reason}</p>
                </div>
            </div>
        </div>
    );
}

function EmptyState({
    icon,
    title,
    description,
    action,
}: {
    icon: ReactNode;
    title: string;
    description: string;
    action?: ReactNode;
}) {
    return (
        <div className="flex flex-1 flex-col justify-center rounded-xl border border-border/70 bg-background px-5 py-6 text-left">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                {icon}
            </div>
            <div className="mt-4 space-y-1.5">
                <h3 className="text-sm font-semibold text-foreground">{title}</h3>
                <p className="text-sm leading-6 text-muted-foreground">{description}</p>
            </div>
            {action ? <div className="mt-5">{action}</div> : null}
        </div>
    );
}

function MessageBubble({
    role,
    text,
    state,
}: {
    role: "user" | "agent";
    text: ReactNode;
    state?: "default" | "muted";
}) {
    const isUser = role === "user";
    const isMuted = state === "muted";
    return (
        <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
            <div
                className={cn(
                    "max-w-[85%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-sm leading-6",
                    isUser
                        ? "rounded-br-md bg-primary text-primary-foreground"
                        : isMuted
                            ? "rounded-bl-md border border-dashed border-border bg-background text-muted-foreground"
                            : "rounded-bl-md bg-muted text-foreground",
                )}
            >
                {text}
            </div>
        </div>
    );
}

function TypingBubble() {
    return (
        <div className="flex justify-start">
            <div className="rounded-2xl rounded-bl-md bg-muted px-3.5 py-3">
                <div className="flex items-center gap-1">
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:-0.3s]" />
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/60 [animation-delay:-0.15s]" />
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/60" />
                </div>
            </div>
        </div>
    );
}

function stringifyToolResult(result: unknown) {
    if (result == null) return "No result";
    if (typeof result === "string") return result;
    try {
        return JSON.stringify(result);
    } catch {
        return String(result);
    }
}

function extractToolEvents(events: Array<Record<string, unknown>>): TextChatToolEvent[] {
    return events.reduce<TextChatToolEvent[]>((acc, event) => {
        const eventType = event.type;
        const payload = event.payload;
        if (!payload || typeof payload !== "object") {
            return acc;
        }
        const typedPayload = payload as Record<string, unknown>;

        const functionName = typeof typedPayload.function_name === "string"
            ? typedPayload.function_name
            : "tool";

        if (eventType === "tool_call_started") {
            acc.push({ kind: "start", functionName });
            return acc;
        }

        if (eventType === "tool_call_result") {
            acc.push({
                kind: "result",
                functionName,
                resultText: stringifyToolResult(typedPayload.result),
            });
            return acc;
        }

        return acc;
    }, []);
}

function getReplayCursorTurnId(turns: TextChatTurn[], turnId: string): string | null {
    const turnIndex = turns.findIndex((turn) => turn.id === turnId);
    if (turnIndex < 0) {
        throw new Error("Turn not found");
    }
    return turns[turnIndex - 1]?.id ?? null;
}

function ToolEventBubble({ event }: { event: TextChatToolEvent }) {
    return (
        <div className="flex justify-start">
            <div className="max-w-[85%] rounded-2xl rounded-bl-md border border-border/70 bg-background px-3.5 py-2 text-sm leading-6 text-foreground">
                <div className="flex items-center gap-2">
                    <Badge variant="outline" className="h-5 px-1.5 text-[10px] uppercase tracking-[0.14em]">
                        {event.kind === "start" ? "Tool" : "Result"}
                    </Badge>
                    <span className="font-mono text-xs text-muted-foreground">
                        {event.kind === "start"
                            ? `${event.functionName}()`
                            : `${event.functionName} -> ${event.resultText ?? "No result"}`}
                    </span>
                </div>
            </div>
        </div>
    );
}

function EmbeddedVoiceTester({
    workflowId,
    workflowRunId,
    initialContextVariables,
    accessToken,
    onReset,
}: {
    workflowId: number;
    workflowRunId: number;
    initialContextVariables?: Record<string, string>;
    accessToken: string;
    onReset: () => void;
}) {
    const router = useRouter();
    const {
        audioRef,
        connectionActive,
        permissionError,
        isCompleted,
        apiKeyModalOpen,
        setApiKeyModalOpen,
        apiKeyError,
        apiKeyErrorCode,
        workflowConfigError,
        workflowConfigModalOpen,
        setWorkflowConfigModalOpen,
        connectionStatus,
        start,
        stop,
        isStarting,
        feedbackMessages,
    } = useWebSocketRTC({
        workflowId,
        workflowRunId,
        accessToken,
        initialContextVariables,
    });
    const autoStartedRef = useRef(false);

    useEffect(() => {
        if (autoStartedRef.current) {
            return;
        }
        autoStartedRef.current = true;
        void start();
    }, [start]);

    const endButtonLabel = connectionActive
        ? "End Call"
        : isCompleted
            ? "Start Another Test"
            : connectionStatus === "failed"
                ? "Retry Call"
                : "Starting Test...";

    const handleFooterAction = async () => {
        if (connectionActive) {
            stop();
            return;
        }
        if (isCompleted) {
            onReset();
            return;
        }
        if (connectionStatus === "failed") {
            await start();
        }
    };

    return (
        <>
            <div className="min-h-0 flex flex-1 flex-col overflow-hidden rounded-xl border border-border/70 bg-background">
                <div className="min-h-0 flex-1 overflow-hidden bg-muted/15">
                    <RealtimeFeedback
                        mode="live"
                        messages={feedbackMessages}
                        isCallActive={connectionActive}
                        isCallCompleted={isCompleted}
                    />
                </div>

                <div className="border-t border-border/70 bg-background px-4 py-3">
                    <div className="flex flex-col gap-3">
                        <ConnectionStatus connectionStatus={connectionStatus} />
                        {permissionError ? (
                            <p className="text-center text-sm text-destructive">{permissionError}</p>
                        ) : null}
                        <Button
                            onClick={handleFooterAction}
                            disabled={isStarting && connectionStatus !== "failed"}
                            variant={connectionActive ? "destructive" : "default"}
                            className="w-full"
                        >
                            {isStarting && connectionStatus !== "failed" ? (
                                <>
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                    Starting Test...
                                </>
                            ) : connectionActive ? (
                                <>
                                    <Phone className="h-4 w-4" />
                                    {endButtonLabel}
                                </>
                            ) : connectionStatus === "failed" ? (
                                <>
                                    <RefreshCw className="h-4 w-4" />
                                    {endButtonLabel}
                                </>
                            ) : isCompleted ? (
                                <>
                                    <RefreshCw className="h-4 w-4" />
                                    {endButtonLabel}
                                </>
                            ) : (
                                <>
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                    {endButtonLabel}
                                </>
                            )}
                        </Button>
                    </div>
                </div>

                <audio ref={audioRef} autoPlay playsInline className="hidden" />
            </div>

            <ApiKeyErrorDialog
                open={apiKeyModalOpen}
                onOpenChange={setApiKeyModalOpen}
                error={apiKeyError}
                errorCode={apiKeyErrorCode}
                onNavigateToCredits={() => router.push("/api-keys")}
                onNavigateToModelConfig={() => router.push("/model-configurations")}
            />

            <WorkflowConfigErrorDialog
                open={workflowConfigModalOpen}
                onOpenChange={setWorkflowConfigModalOpen}
                error={workflowConfigError}
                onNavigateToWorkflow={() => router.push(`/workflow/${workflowId}`)}
            />
        </>
    );
}

function ManualTextChat({
    workflowId,
    ready,
    initialContextVariables,
    disabled,
    disabledReason,
    onActiveChange,
}: {
    workflowId: number;
    ready: boolean;
    initialContextVariables?: Record<string, string>;
    disabled: boolean;
    disabledReason: string | null;
    onActiveChange?: (active: boolean) => void;
}) {
    const [session, setSession] = useState<TextChatSession | null>(null);
    const [started, setStarted] = useState(false);
    const [draft, setDraft] = useState("");
    const [creatingSession, setCreatingSession] = useState(false);
    const [sendingMessage, setSendingMessage] = useState(false);
    const [editingTurnId, setEditingTurnId] = useState<string | null>(null);
    const [activeTurnAction, setActiveTurnAction] = useState<TurnActionState | null>(null);
    const scrollEndRef = useRef<HTMLDivElement | null>(null);

    const turns = session?.session_data.turns ?? EMPTY_TEXT_CHAT_TURNS;
    const editingTurn = editingTurnId
        ? turns.find((turn) => turn.id === editingTurnId) ?? null
        : null;
    const composerId = `workflow-tester-compose-${workflowId}`;

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

    const submitMessage = useCallback(async (
        messageText: string,
        replayOptions?: TurnActionState,
    ) => {
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
                        cursor_turn_id: getReplayCursorTurnId(
                            activeSession.session_data.turns,
                            replayOptions.turnId,
                        ),
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

    useEffect(() => {
        if (!editingTurnId) {
            return;
        }
        if (!turns.some((turn) => turn.id === editingTurnId)) {
            setEditingTurnId(null);
            setDraft("");
        }
    }, [editingTurnId, turns]);

    useEffect(() => {
        scrollEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [session?.revision, sendingMessage, turns.length]);

    const inputDisabled = disabled || !session;

    if (!started && !session) {
        return (
            <div className="flex h-full min-h-0 flex-col gap-3">
                {disabledReason ? <DisabledNotice reason={disabledReason} /> : null}
                <EmptyState
                    icon={<MessageSquareText className="h-7 w-7" />}
                    title="Chat with this agent"
                    description="Test the agent over a text conversation. Send messages and see how it responds, with tool calls and rewind support."
                    action={
                        <Button onClick={() => setStarted(true)} disabled={disabled || !ready}>
                            <MessageSquareText className="h-4 w-4" />
                            Start Test
                        </Button>
                    }
                />
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

            <div className="min-h-0 flex-1 overflow-y-auto">
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
                    <div className="space-y-3 py-1">
                        {turns.map((turn) => {
                            const toolEvents = extractToolEvents(turn.events);
                            const rewindingThisTurn = activeTurnAction?.turnId === turn.id && activeTurnAction.type === "rewind";
                            const rerunningEditedTurn = activeTurnAction?.turnId === turn.id && activeTurnAction.type === "edit";
                            return (
                                <div key={turn.id} className="group space-y-1.5">
                                    {turn.user_message ? (
                                        <div className="space-y-1">
                                            <MessageBubble role="user" text={turn.user_message.text} />
                                            <div className="flex h-5 items-center justify-end gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                                                <button
                                                    type="button"
                                                    onClick={() => void rewindTurn(turn)}
                                                    disabled={disabled || sendingMessage}
                                                    aria-label="Rerun this turn"
                                                    title="Rerun this turn"
                                                    className="inline-flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
                                                >
                                                    {rewindingThisTurn ? (
                                                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                    ) : (
                                                        <RotateCcw className="h-3.5 w-3.5" />
                                                    )}
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={() => startEditingTurn(turn)}
                                                    disabled={disabled || sendingMessage}
                                                    aria-label="Edit and rerun this turn"
                                                    title="Edit and rerun this turn"
                                                    className={cn(
                                                        "inline-flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50",
                                                        editingTurnId === turn.id && "bg-muted text-foreground",
                                                    )}
                                                >
                                                    {rerunningEditedTurn ? (
                                                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                    ) : (
                                                        <Pencil className="h-3.5 w-3.5" />
                                                    )}
                                                </button>
                                            </div>
                                        </div>
                                    ) : null}
                                    {toolEvents.map((event, index) => (
                                        <ToolEventBubble
                                            key={`${turn.id}-${event.kind}-${event.functionName}-${index}`}
                                            event={event}
                                        />
                                    ))}
                                    {turn.assistant_message ? (
                                        <MessageBubble role="agent" text={turn.assistant_message.text} />
                                    ) : turn.status === "failed" ? (
                                        <MessageBubble role="agent" state="muted" text="Agent turn failed" />
                                    ) : null}
                                </div>
                            );
                        })}
                        {sendingMessage ? <TypingBubble /> : null}
                        <div ref={scrollEndRef} />
                    </div>
                )}
            </div>

            <div className="pt-3">
                {editingTurn ? (
                    <div className="mb-2 flex items-center justify-between gap-2 rounded-lg border border-border/70 bg-muted/35 px-3 py-2 text-xs text-muted-foreground">
                        <span>Edit the selected user message, then press Enter to rerun from that point.</span>
                        <button
                            type="button"
                            onClick={cancelEditingTurn}
                            className="inline-flex items-center gap-1 rounded text-foreground hover:text-foreground/80 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                        >
                            <X className="h-3.5 w-3.5" />
                            Cancel
                        </button>
                    </div>
                ) : null}
                <div className="relative">
                    <Textarea
                        id={composerId}
                        value={draft}
                        onChange={(event) => setDraft(event.target.value)}
                        placeholder={ready ? (editingTurn ? "Edit and rerun this message…" : "Send a message…") : "Preparing chat…"}
                        rows={1}
                        className="min-h-11! resize-none pr-20 text-sm leading-6"
                        disabled={inputDisabled}
                        onKeyDown={(event) => {
                            if (event.key === "Enter" && !event.shiftKey) {
                                event.preventDefault();
                                if (sendingMessage) return;
                                void submitComposer();
                            }
                        }}
                    />
                    <Button
                        type="button"
                        size="sm"
                        onClick={() => void submitComposer()}
                        disabled={inputDisabled || sendingMessage || !draft.trim()}
                        className="absolute bottom-1.5 right-1.5 h-8 px-4"
                    >
                        {sendingMessage ? (
                            <>
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                {editingTurn ? "Rerunning" : "Sending"}
                            </>
                        ) : (
                            editingTurn ? "Rerun" : "Send"
                        )}
                    </Button>
                </div>
            </div>
        </div>
    );
}

function AiSimulatorPlaceholder({
    disabledReason,
}: {
    disabledReason: string | null;
}) {
    const [simulatorPrompt, setSimulatorPrompt] = useState(
        "Act like a skeptical prospect. Push on pricing, ask about integrations, and end the chat if the assistant becomes repetitive."
    );

    return (
        <div className="flex min-h-0 flex-1 flex-col gap-3">
            {disabledReason ? <DisabledNotice reason={disabledReason} /> : null}
            <p className="text-sm text-muted-foreground">
                Drive multi-turn, agent-vs-agent tests with a persona prompt.
            </p>
            <Textarea
                value={simulatorPrompt}
                onChange={(event) => setSimulatorPrompt(event.target.value)}
                placeholder="Describe the simulated user…"
                className="min-h-32 resize-none text-sm leading-6"
            />
            <Button size="sm" disabled className="self-start">
                <Sparkles className="h-4 w-4" />
                Coming soon
            </Button>
        </div>
    );
}

function ChatModeToggle({
    value,
    onChange,
}: {
    value: "manual" | "simulated";
    onChange: (next: "manual" | "simulated") => void;
}) {
    const options: Array<{ id: "manual" | "simulated"; label: string }> = [
        { id: "manual", label: "Manual" },
        { id: "simulated", label: "Simulated" },
    ];
    return (
        <div className="inline-flex items-center gap-0.5 rounded-md border border-border/70 bg-muted/40 p-0.5">
            {options.map((option) => {
                const active = option.id === value;
                return (
                    <button
                        key={option.id}
                        type="button"
                        onClick={() => onChange(option.id)}
                        className={cn(
                            "rounded-[5px] px-2.5 py-1 text-xs font-medium transition",
                            active
                                ? "bg-background text-foreground shadow-xs"
                                : "text-muted-foreground hover:text-foreground",
                        )}
                    >
                        {option.label}
                    </button>
                );
            })}
        </div>
    );
}

export function WorkflowTesterPanel({
    workflowId,
    initialContextVariables,
    disabled,
    disabledReason,
    className,
    onClose,
}: WorkflowTesterPanelProps) {
    const auth = useAuth();
    const { isAuthenticated, loading: authLoading, getAccessToken } = auth;
    const [accessToken, setAccessToken] = useState<string | null>(null);
    const [activeMode, setActiveMode] = useState<"audio" | "text">("audio");
    const [chatMode, setChatMode] = useState<"manual" | "simulated">("manual");
    const [chatSessionKey, setChatSessionKey] = useState(0);
    const [chatActive, setChatActive] = useState(false);
    const [voiceRunId, setVoiceRunId] = useState<number | null>(null);
    const [creatingVoiceRun, setCreatingVoiceRun] = useState(false);
    const [tokenReady, setTokenReady] = useState(false);

    useEffect(() => {
        let ignore = false;

        const hydrateAccessToken = async () => {
            if (!isAuthenticated || authLoading) return;
            try {
                const token = await getAccessToken();
                if (!ignore) {
                    setAccessToken(token);
                }
            } catch (error) {
                if (!ignore) {
                    toast.error(getErrorMessage(error));
                }
            } finally {
                if (!ignore) {
                    setTokenReady(true);
                }
            }
        };

        if (authLoading) {
            return;
        }

        if (!isAuthenticated) {
            setTokenReady(true);
            return;
        }

        hydrateAccessToken();

        return () => {
            ignore = true;
        };
    }, [authLoading, getAccessToken, isAuthenticated]);

    const createVoiceRun = useCallback(async () => {
        if (!accessToken || disabled) return;
        setCreatingVoiceRun(true);
        try {
            const response = await createWorkflowRunApiV1WorkflowWorkflowIdRunsPost({
                path: { workflow_id: workflowId },
                body: {
                    mode: WORKFLOW_RUN_MODES.SMALL_WEBRTC,
                    name: `WR-${getRandomId()}`,
                },
            });

            if (response.error || !response.data?.id) {
                throw new Error(extractSdkErrorMessage(response.error, "Failed to create browser test run"));
            }

            setVoiceRunId(response.data.id);
            setActiveMode("audio");
        } catch (error) {
            toast.error(getErrorMessage(error));
        } finally {
            setCreatingVoiceRun(false);
        }
    }, [accessToken, disabled, workflowId]);

    const authUnavailableReason = tokenReady && !accessToken
        ? "Authentication is required before testing can start."
        : null;
    const effectiveDisabledReason = disabledReason ?? authUnavailableReason;
    const testerBlocked = disabled || authUnavailableReason !== null;

    return (
        <div className={cn("flex h-full min-h-0 flex-col bg-background", className)}>
            <Tabs
                value={activeMode}
                onValueChange={(value) => setActiveMode(value as "audio" | "text")}
                className="min-h-0 flex-1 gap-0"
            >
                <div className="border-b border-border/70 px-4 py-3">
                    <div className="flex items-center gap-3">
                        <TabsList className="grid h-9 flex-1 grid-cols-2 rounded-lg bg-muted/60 p-1">
                            <TabsTrigger value="audio" className="rounded-md text-sm">
                                <Mic className="h-4 w-4" />
                                Test Audio
                            </TabsTrigger>
                            <TabsTrigger value="text" className="rounded-md text-sm">
                                <MessageSquareText className="h-4 w-4" />
                                Test Chat
                            </TabsTrigger>
                        </TabsList>
                        {onClose ? (
                            <Button
                                variant="ghost"
                                size="icon"
                                onClick={onClose}
                                className="shrink-0 text-muted-foreground hover:text-foreground"
                                aria-label="Close tester panel"
                            >
                                <X className="h-4 w-4" />
                            </Button>
                        ) : null}
                    </div>
                </div>

                <TabsContent value="audio" className="min-h-0 flex-1 px-4 py-4">
                    <div className="flex h-full min-h-0 flex-col gap-3">
                        {!tokenReady ? (
                            <div className="space-y-4">
                                <Skeleton className="h-14 rounded-xl" />
                                <Skeleton className="h-80 rounded-xl" />
                            </div>
                        ) : !accessToken ? (
                            <DisabledNotice reason={authUnavailableReason ?? "Authentication is required before browser tests can start."} />
                        ) : voiceRunId ? (
                            <EmbeddedVoiceTester
                                workflowId={workflowId}
                                workflowRunId={voiceRunId}
                                initialContextVariables={initialContextVariables}
                                accessToken={accessToken}
                                onReset={() => setVoiceRunId(null)}
                            />
                        ) : (
                            <>
                                {effectiveDisabledReason ? <DisabledNotice reason={effectiveDisabledReason} /> : null}
                                <EmptyState
                                    icon={<Phone className="h-7 w-7" />}
                                    title="Call this agent in the browser"
                                    description="Test the Agent over a Voice Call. Some tools which work over telephony, like Transfer Calls are not yet supported."
                                    action={
                                        <Button onClick={createVoiceRun} disabled={creatingVoiceRun || testerBlocked}>
                                            {creatingVoiceRun ? (
                                                <>
                                                    <Loader2 className="h-4 w-4 animate-spin" />
                                                    Starting test...
                                                </>
                                            ) : (
                                                <>
                                                    <Phone className="h-4 w-4" />
                                                    Run Test
                                                </>
                                            )}
                                        </Button>
                                    }
                                />
                            </>
                        )}
                    </div>
                </TabsContent>

                <TabsContent value="text" className="min-h-0 flex-1 px-4 py-3">
                    <div className="flex h-full min-h-0 flex-col gap-3">
                        <div className="flex items-center justify-between gap-2">
                            <ChatModeToggle value={chatMode} onChange={setChatMode} />
                            {chatMode === "manual" && chatActive ? (
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => setChatSessionKey((k) => k + 1)}
                                    disabled={testerBlocked}
                                    className="h-7 px-2 text-xs text-muted-foreground hover:text-foreground"
                                >
                                    <RefreshCw className="h-3.5 w-3.5" />
                                    Reset
                                </Button>
                            ) : null}
                        </div>

                        {chatMode === "manual" ? (
                            <ManualTextChat
                                key={chatSessionKey}
                                workflowId={workflowId}
                                ready={tokenReady && !!accessToken}
                                initialContextVariables={initialContextVariables}
                                disabled={testerBlocked}
                                disabledReason={effectiveDisabledReason}
                                onActiveChange={setChatActive}
                            />
                        ) : (
                            <AiSimulatorPlaceholder disabledReason={effectiveDisabledReason} />
                        )}
                    </div>
                </TabsContent>
            </Tabs>
        </div>
    );
}
