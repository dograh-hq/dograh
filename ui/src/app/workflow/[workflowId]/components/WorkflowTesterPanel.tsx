"use client";

import { AlertCircle, ArrowLeft, ArrowUpRight, Bot, Loader2, MessageSquareText, Mic, Phone, RefreshCw, RotateCcw, Sparkles, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { client } from "@/client/client.gen";
import { createWorkflowRunApiV1WorkflowWorkflowIdRunsPost } from "@/client/sdk.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
    user_message: TextChatMessage;
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

interface TextChatSessionResponse {
    workflow_run_id: number;
    workflow_id: number;
    name: string;
    mode: string;
    state: string;
    is_completed: boolean;
    revision: number;
    initial_context: Record<string, unknown> | null;
    gathered_context: Record<string, unknown> | null;
    annotations: Record<string, unknown> | null;
    session_data: TextChatSessionData;
    checkpoint: TextChatCheckpoint;
    created_at: string;
    updated_at: string | null;
}

function formatTimestamp(timestamp: string | null | undefined) {
    if (!timestamp) return "";
    const parsed = new Date(timestamp);
    if (Number.isNaN(parsed.getTime())) return "";
    return parsed.toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
    });
}

function getErrorMessage(error: unknown) {
    if (error instanceof Error) return error.message;
    return "Something went wrong";
}

async function readErrorMessage(response: Response) {
    const payload = await response.json().catch(() => null) as
        | { detail?: string | { message?: string } }
        | null;
    if (typeof payload?.detail === "string") return payload.detail;
    if (typeof payload?.detail === "object" && payload.detail?.message) {
        return payload.detail.message;
    }
    return `Request failed with ${response.status}`;
}

function DisabledNotice({ reason }: { reason: string }) {
    return (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
            <div className="flex items-start gap-3">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                <div className="space-y-1">
                    <p className="font-medium">Testing is paused</p>
                    <p className="text-amber-800 dark:text-amber-300">{reason}</p>
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
        <div className="flex flex-1 flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-card px-6 py-10 text-center">
            <div className="flex h-16 w-16 items-center justify-center rounded-full bg-muted text-muted-foreground">
                {icon}
            </div>
            <div className="mt-5 space-y-2">
                <h3 className="text-base font-semibold text-foreground">{title}</h3>
                <p className="text-sm leading-6 text-muted-foreground">{description}</p>
            </div>
            {action ? <div className="mt-6">{action}</div> : null}
        </div>
    );
}

function TextChatModeCard({
    title,
    description,
    preview,
    actionLabel,
    icon,
    onClick,
}: {
    title: string;
    description: string;
    preview: ReactNode;
    actionLabel: string;
    icon: ReactNode;
    onClick: () => void;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            className="w-full rounded-2xl border border-border bg-card p-4 text-left shadow-sm transition hover:border-foreground/20 hover:shadow-md"
        >
            <div className="space-y-4">
                <div className="rounded-2xl border border-border bg-muted/40 p-4">
                    {preview}
                </div>
                <div className="flex items-start justify-between gap-4">
                    <div className="space-y-1">
                        <div className="text-base font-semibold text-foreground">{title}</div>
                        <p className="text-sm leading-6 text-muted-foreground">{description}</p>
                    </div>
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
                        {icon}
                    </div>
                </div>
                <div className="text-sm font-medium text-foreground">{actionLabel}</div>
            </div>
        </button>
    );
}

function TextChatLanding({
    onSelectManual,
    onSelectSimulated,
}: {
    onSelectManual: () => void;
    onSelectSimulated: () => void;
}) {
    return (
        <div className="space-y-4">
            <TextChatModeCard
                title="Manual Chat"
                description="Manually chat with the agent."
                actionLabel="Open Manual Chat"
                icon={<MessageSquareText className="h-5 w-5" />}
                onClick={onSelectManual}
                preview={
                    <div className="space-y-2">
                        <div className="flex justify-end">
                            <div className="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-3 py-2 text-sm text-primary-foreground">
                                How are you doing?
                            </div>
                        </div>
                        <div className="flex justify-start">
                            <div className="max-w-[85%] rounded-2xl rounded-bl-md bg-card px-3 py-2 text-sm text-card-foreground shadow-sm">
                                I am doing well.
                            </div>
                        </div>
                    </div>
                }
            />
            <TextChatModeCard
                title="AI Simulated Chat"
                description="Use a prompt to simulate user responses."
                actionLabel="Configure Simulation"
                icon={<Sparkles className="h-5 w-5" />}
                onClick={onSelectSimulated}
                preview={
                    <div className="space-y-2">
                        <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                            User Prompt
                        </div>
                        <div className="rounded-xl border border-border bg-card px-3 py-2 text-sm text-foreground">
                            You are a customer who wants to return a package...
                        </div>
                    </div>
                }
            />
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
            <div className="min-h-0 flex flex-1 flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
                <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
                    <div className="space-y-1">
                        <div className="flex items-center gap-2">
                            <Badge variant="outline">
                                Run {workflowRunId}
                            </Badge>
                            <Badge variant="outline">
                                Browser Voice Test
                            </Badge>
                        </div>
                        <CardDescription>
                            The call starts as soon as this test run is created.
                        </CardDescription>
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

                <div className="min-h-0 flex-1 overflow-hidden">
                    <RealtimeFeedback
                        mode="live"
                        messages={feedbackMessages}
                        isCallActive={connectionActive}
                        isCallCompleted={isCompleted}
                    />
                </div>

                <div className="border-t border-border px-4 py-3">
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
    accessToken,
    initialContextVariables,
    disabled,
    disabledReason,
    onBack,
}: {
    workflowId: number;
    accessToken: string | null;
    initialContextVariables?: Record<string, string>;
    disabled: boolean;
    disabledReason: string | null;
    onBack: () => void;
}) {
    const [session, setSession] = useState<TextChatSessionResponse | null>(null);
    const [draft, setDraft] = useState("");
    const [creatingSession, setCreatingSession] = useState(false);
    const [sendingMessage, setSendingMessage] = useState(false);
    const [rewindingTurnId, setRewindingTurnId] = useState<string | null>(null);

    const turns = session?.session_data.turns ?? [];
    const hasTurns = turns.length > 0;

    const request = useCallback(async (
        path: string,
        init?: RequestInit,
    ): Promise<TextChatSessionResponse> => {
        if (!accessToken) {
            throw new Error("Authentication is still loading");
        }

        const baseUrl = client.getConfig().baseUrl || window.location.origin;
        const response = await fetch(`${baseUrl}${path}`, {
            ...init,
            headers: {
                "Authorization": `Bearer ${accessToken}`,
                "Content-Type": "application/json",
                ...(init?.headers ?? {}),
            },
        });

        if (!response.ok) {
            throw new Error(await readErrorMessage(response));
        }

        return response.json() as Promise<TextChatSessionResponse>;
    }, [accessToken]);

    const createSession = useCallback(async () => {
        if (disabled) return;
        setCreatingSession(true);
        try {
            const created = await request(`/api/v1/workflow/${workflowId}/text-chat/sessions`, {
                method: "POST",
                body: JSON.stringify({
                    initial_context: initialContextVariables ?? {},
                    annotations: {
                        tester: {
                            source: "workflow_editor",
                            modality: "text",
                            ui_mode: "manual_text",
                        },
                    },
                }),
            });
            setSession(created);
            setDraft("");
        } catch (error) {
            toast.error(getErrorMessage(error));
        } finally {
            setCreatingSession(false);
        }
    }, [disabled, initialContextVariables, request, workflowId]);

    useEffect(() => {
        if (creatingSession || session || !accessToken || disabled) {
            return;
        }
        void createSession();
    }, [accessToken, createSession, creatingSession, disabled, session]);

    const sendMessage = useCallback(async () => {
        if (!session || !draft.trim() || disabled) return;
        setSendingMessage(true);
        try {
            const updated = await request(
                `/api/v1/workflow/${workflowId}/text-chat/sessions/${session.workflow_run_id}/messages`,
                {
                    method: "POST",
                    body: JSON.stringify({
                        text: draft.trim(),
                        expected_revision: session.revision,
                    }),
                },
            );
            setSession(updated);
            setDraft("");
        } catch (error) {
            toast.error(getErrorMessage(error));
        } finally {
            setSendingMessage(false);
        }
    }, [disabled, draft, request, session, workflowId]);

    const rewindToTurn = useCallback(async (turnId: string) => {
        if (!session || disabled) return;
        setRewindingTurnId(turnId);
        try {
            const updated = await request(
                `/api/v1/workflow/${workflowId}/text-chat/sessions/${session.workflow_run_id}/rewind`,
                {
                    method: "POST",
                    body: JSON.stringify({
                        cursor_turn_id: turnId,
                        expected_revision: session.revision,
                    }),
                },
            );
            setSession(updated);
        } catch (error) {
            toast.error(getErrorMessage(error));
        } finally {
            setRewindingTurnId(null);
        }
    }, [disabled, request, session, workflowId]);

    if (creatingSession && !session) {
        return (
            <div className="space-y-4">
                <Skeleton className="h-24 rounded-2xl" />
                <Skeleton className="h-56 rounded-2xl" />
                <Skeleton className="h-24 rounded-2xl" />
            </div>
        );
    }

    return (
        <div className="flex min-h-0 flex-1 flex-col gap-4">
            <div className="flex items-start justify-between gap-3">
                <div className="space-y-1">
                    <div className="text-lg font-semibold text-foreground">Manual Chat</div>
                    <p className="text-sm leading-6 text-muted-foreground">Manually chat with the agent.</p>
                </div>
                <div className="flex gap-2">
                    <Button variant="ghost" size="sm" onClick={onBack} className="text-muted-foreground">
                        <ArrowLeft className="h-4 w-4" />
                        Back
                    </Button>
                    {session ? (
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={createSession}
                            disabled={creatingSession || disabled}
                        >
                            <RefreshCw className="h-4 w-4" />
                            New Session
                        </Button>
                    ) : null}
                </div>
            </div>

            {disabledReason ? <DisabledNotice reason={disabledReason} /> : null}

            {!session ? (
                <EmptyState
                    icon={<MessageSquareText className="h-7 w-7" />}
                    title={disabled ? "Manual chat is paused" : "Preparing chat"}
                    description={disabled
                        ? (disabledReason ?? "Save the draft before starting a manual chat.")
                        : "Creating a durable text session for this workflow."}
                />
            ) : (
                <div className="min-h-0 flex flex-1 flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
                    <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
                        <div className="text-sm text-muted-foreground">
                            {turns.length === 0
                                ? "Send the first message to begin testing."
                                : "Continue the conversation or rewind from any previous turn."}
                        </div>
                        {session.session_data.discarded_future.length > 0 ? (
                            <Badge variant="outline">
                                {session.session_data.discarded_future.length} archived branch{session.session_data.discarded_future.length === 1 ? "" : "es"}
                            </Badge>
                        ) : null}
                    </div>

                    <div className="min-h-0 flex-1 overflow-y-auto p-4">
                        <div className="space-y-5">
                            {hasTurns ? turns.map((turn) => {
                                const isResumePoint = session.session_data.cursor_turn_id === turn.id;
                                return (
                                    <div key={turn.id} className="space-y-3 rounded-2xl border border-border bg-muted/40 p-4">
                                        <div className="flex items-center justify-between gap-3">
                                            <div className="flex items-center gap-2">
                                                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                                                    {turn.id.replace("turn_", "Turn ")}
                                                </p>
                                                {isResumePoint ? (
                                                    <Badge variant="secondary">Resume point</Badge>
                                                ) : null}
                                            </div>
                                            <div className="flex items-center gap-2">
                                                <span className="text-xs text-muted-foreground">{formatTimestamp(turn.created_at)}</span>
                                                <Button
                                                    variant="ghost"
                                                    size="sm"
                                                    onClick={() => rewindToTurn(turn.id)}
                                                    disabled={disabled || rewindingTurnId === turn.id}
                                                    className="h-7 px-2 text-muted-foreground"
                                                >
                                                    {rewindingTurnId === turn.id ? (
                                                        <Loader2 className="h-4 w-4 animate-spin" />
                                                    ) : (
                                                        <RotateCcw className="h-4 w-4" />
                                                    )}
                                                    Resume Here
                                                </Button>
                                            </div>
                                        </div>

                                        <div className="flex justify-end">
                                            <div className="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-4 py-3 text-sm text-primary-foreground">
                                                <p className="leading-6">{turn.user_message.text}</p>
                                            </div>
                                        </div>

                                        <div className="flex justify-start">
                                            <div className={cn(
                                                "max-w-[85%] rounded-2xl rounded-bl-md px-4 py-3 text-sm",
                                                turn.assistant_message
                                                    ? "bg-card text-card-foreground shadow-sm"
                                                    : "border border-dashed border-border bg-card text-muted-foreground",
                                            )}>
                                                {turn.assistant_message ? (
                                                    <p className="leading-6">{turn.assistant_message.text}</p>
                                                ) : (
                                                    <div className="space-y-1.5">
                                                        <p className="font-medium">Assistant turn pending</p>
                                                        <p className="text-xs leading-5 text-muted-foreground">
                                                            The durable session was saved. The executor that fills assistant replies lands in the next pass.
                                                        </p>
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                );
                            }) : (
                                <EmptyState
                                    icon={<Bot className="h-7 w-7" />}
                                    title="Session created"
                                    description="Send the first user message to populate this test thread."
                                />
                            )}
                        </div>
                    </div>

                    <div className="border-t border-border p-4">
                        <div className="space-y-4">
                            <Textarea
                                value={draft}
                                onChange={(event) => setDraft(event.target.value)}
                                placeholder="Write the next user message..."
                                className="min-h-24"
                                disabled={sendingMessage || disabled}
                                onKeyDown={(event) => {
                                    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                                        event.preventDefault();
                                        void sendMessage();
                                    }
                                }}
                            />

                            <div className="flex items-center justify-between gap-3">
                                <p className="text-xs text-muted-foreground">
                                    Press Cmd/Ctrl + Enter to send.
                                </p>
                                <Button
                                    onClick={sendMessage}
                                    disabled={sendingMessage || !draft.trim() || disabled}
                                >
                                    {sendingMessage ? (
                                        <>
                                            <Loader2 className="h-4 w-4 animate-spin" />
                                            Sending...
                                        </>
                                    ) : (
                                        "Send Message"
                                    )}
                                </Button>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

function AiSimulatorPlaceholder({
    disabledReason,
    onBack,
}: {
    disabledReason: string | null;
    onBack: () => void;
}) {
    const [simulatorPrompt, setSimulatorPrompt] = useState(
        "Act like a skeptical prospect. Push on pricing, ask about integrations, and end the chat if the assistant becomes repetitive."
    );

    return (
        <div className="space-y-4">
            <div className="flex items-start justify-between gap-3">
                <div className="space-y-1">
                    <div className="text-lg font-semibold text-foreground">AI Simulated Chat</div>
                    <p className="text-sm leading-6 text-muted-foreground">Use a prompt to simulate user responses.</p>
                </div>
                <Button variant="ghost" size="sm" onClick={onBack} className="text-muted-foreground">
                    <ArrowLeft className="h-4 w-4" />
                    Back
                </Button>
            </div>
            {disabledReason ? <DisabledNotice reason={disabledReason} /> : null}
            <Card className="border-border shadow-sm">
                <CardHeader>
                    <div className="flex items-start gap-3">
                        <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-muted text-muted-foreground">
                            <Sparkles className="h-5 w-5" />
                        </div>
                        <div className="space-y-1">
                            <CardTitle className="text-lg text-foreground">AI Simulated Chat</CardTitle>
                            <CardDescription>
                                This will drive multi-turn agent-vs-agent tests on top of the same durable text session model.
                            </CardDescription>
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="space-y-4">
                    <Textarea
                        value={simulatorPrompt}
                        onChange={(event) => setSimulatorPrompt(event.target.value)}
                        className="min-h-32"
                    />
                    <div className="rounded-xl border border-border bg-muted/40 p-4 text-sm text-muted-foreground">
                        The next increment will persist simulator configuration here, queue turns against the same session, and let you stop or resume the simulation without losing context.
                    </div>
                    <Button disabled>
                        <Sparkles className="h-4 w-4" />
                        Simulation Wiring Next
                    </Button>
                </CardContent>
            </Card>
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
    const [textMode, setTextMode] = useState<"landing" | "manual" | "simulated">("landing");
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
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
                body: {
                    mode: WORKFLOW_RUN_MODES.SMALL_WEBRTC,
                    name: `WR-${getRandomId()}`,
                },
            });

            if (response.error || !response.data?.id) {
                throw new Error("Failed to create browser test run");
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
                className="min-h-0 flex-1"
            >
                <div className="border-b border-border px-5 py-4">
                    <div className="flex items-center gap-3">
                        <TabsList className="grid flex-1 grid-cols-2 rounded-xl bg-muted p-1">
                            <TabsTrigger value="audio" className="rounded-lg">
                                <Mic className="h-4 w-4" />
                                Test Audio
                            </TabsTrigger>
                            <TabsTrigger value="text" className="rounded-lg">
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

                <TabsContent value="audio" className="min-h-0 flex-1 px-5 py-5">
                    <div className="flex h-full min-h-0 flex-col gap-4">
                        {!tokenReady ? (
                            <div className="space-y-4">
                                <Skeleton className="h-28 rounded-2xl" />
                                <Skeleton className="h-80 rounded-2xl" />
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

                <TabsContent value="text" className="min-h-0 flex-1 px-5 py-5">
                    {textMode === "landing" ? (
                        <TextChatLanding
                            onSelectManual={() => setTextMode("manual")}
                            onSelectSimulated={() => setTextMode("simulated")}
                        />
                    ) : textMode === "manual" ? (
                        <div className="min-h-0 flex-1">
                            <ManualTextChat
                                workflowId={workflowId}
                                accessToken={accessToken}
                                initialContextVariables={initialContextVariables}
                                disabled={testerBlocked}
                                disabledReason={effectiveDisabledReason}
                                onBack={() => setTextMode("landing")}
                            />
                        </div>
                    ) : (
                        <AiSimulatorPlaceholder
                            disabledReason={effectiveDisabledReason}
                            onBack={() => setTextMode("landing")}
                        />
                    )}
                </TabsContent>
            </Tabs>
        </div>
    );
}
