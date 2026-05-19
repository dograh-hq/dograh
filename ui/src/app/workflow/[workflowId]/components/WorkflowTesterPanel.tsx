"use client";

import { AlertCircle, ArrowUpRight, Loader2, MessageSquareText, Mic, Phone, RefreshCw, RotateCcw, Sparkles, X } from "lucide-react";
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
                <div className="flex items-start justify-between gap-3 border-b border-border/70 px-4 py-3">
                    <div className="space-y-1">
                        <div className="flex flex-wrap items-center gap-2">
                            <Badge variant="outline" className="font-medium">
                                Run {workflowRunId}
                            </Badge>
                            <Badge variant="outline" className="font-medium">
                                Browser voice test
                            </Badge>
                        </div>
                        <p className="text-sm text-muted-foreground">
                            The call starts as soon as this test run is created.
                        </p>
                    </div>
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => router.push(`/workflow/${workflowId}/run/${workflowRunId}`)}
                    >
                        <ArrowUpRight className="h-4 w-4" />
                        Open Run
                    </Button>
                </div>

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
}: {
    workflowId: number;
    ready: boolean;
    initialContextVariables?: Record<string, string>;
    disabled: boolean;
    disabledReason: string | null;
}) {
    const [session, setSession] = useState<TextChatSession | null>(null);
    const [draft, setDraft] = useState("");
    const [creatingSession, setCreatingSession] = useState(false);
    const [sendingMessage, setSendingMessage] = useState(false);
    const [rewindingTurnId, setRewindingTurnId] = useState<string | null>(null);
    const scrollEndRef = useRef<HTMLDivElement | null>(null);

    const turns = session?.session_data.turns ?? [];

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
        if (creatingSession || session || !ready || disabled) {
            return;
        }
        void createSession();
    }, [createSession, creatingSession, disabled, ready, session]);

    const sendMessage = useCallback(async () => {
        if (!session || !draft.trim() || disabled) return;
        setSendingMessage(true);
        try {
            const response = await appendTextChatMessageApiV1WorkflowWorkflowIdTextChatSessionsRunIdMessagesPost({
                path: { workflow_id: workflowId, run_id: session.workflow_run_id },
                body: {
                    text: draft.trim(),
                    expected_revision: session.revision,
                },
            });
            if (response.error || !response.data) {
                throw new Error(extractSdkErrorMessage(response.error, "Failed to send message"));
            }
            setSession(toTextChatSession(response.data));
            setDraft("");
        } catch (error) {
            toast.error(getErrorMessage(error));
        } finally {
            setSendingMessage(false);
        }
    }, [disabled, draft, session, workflowId]);

    const rewindToTurn = useCallback(async (turnId: string) => {
        if (!session || disabled) return;
        setRewindingTurnId(turnId);
        try {
            const response = await rewindTextChatSessionApiV1WorkflowWorkflowIdTextChatSessionsRunIdRewindPost({
                path: { workflow_id: workflowId, run_id: session.workflow_run_id },
                body: {
                    cursor_turn_id: turnId,
                    expected_revision: session.revision,
                },
            });
            if (response.error || !response.data) {
                throw new Error(extractSdkErrorMessage(response.error, "Failed to rewind session"));
            }
            setSession(toTextChatSession(response.data));
        } catch (error) {
            toast.error(getErrorMessage(error));
        } finally {
            setRewindingTurnId(null);
        }
    }, [disabled, session, workflowId]);

    useEffect(() => {
        scrollEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [turns.length, sendingMessage]);

    const inputDisabled = disabled || !session;

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
                        {turns.map((turn) => (
                            <div key={turn.id} className="group space-y-1.5">
                                {turn.user_message ? (
                                    <MessageBubble role="user" text={turn.user_message.text} />
                                ) : null}
                                {turn.assistant_message ? (
                                    <MessageBubble role="agent" text={turn.assistant_message.text} />
                                ) : turn.status === "failed" ? (
                                    <MessageBubble role="agent" state="muted" text="Agent turn failed" />
                                ) : null}
                                <div className="flex h-4 items-center justify-end opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                                    <button
                                        type="button"
                                        onClick={() => rewindToTurn(turn.id)}
                                        disabled={disabled || rewindingTurnId === turn.id}
                                        className="inline-flex items-center gap-1 rounded text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                                    >
                                        {rewindingTurnId === turn.id ? (
                                            <Loader2 className="h-3 w-3 animate-spin" />
                                        ) : (
                                            <RotateCcw className="h-3 w-3" />
                                        )}
                                        Rewind here
                                    </button>
                                </div>
                            </div>
                        ))}
                        {sendingMessage ? <TypingBubble /> : null}
                        <div ref={scrollEndRef} />
                    </div>
                )}
            </div>

            <div className="pt-3">
                <div className="relative">
                    <Textarea
                        value={draft}
                        onChange={(event) => setDraft(event.target.value)}
                        placeholder={ready ? "Send a message…" : "Preparing chat…"}
                        rows={1}
                        className="min-h-11! resize-none pr-20 text-sm leading-6"
                        disabled={inputDisabled}
                        onKeyDown={(event) => {
                            if (event.key === "Enter" && !event.shiftKey) {
                                event.preventDefault();
                                if (sendingMessage) return;
                                void sendMessage();
                            }
                        }}
                    />
                    <Button
                        type="button"
                        size="sm"
                        onClick={sendMessage}
                        disabled={inputDisabled || sendingMessage || !draft.trim()}
                        className="absolute bottom-1.5 right-1.5 h-8 px-4"
                    >
                        {sendingMessage ? (
                            <>
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                Sending
                            </>
                        ) : (
                            "Send"
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
                            {chatMode === "manual" ? (
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
