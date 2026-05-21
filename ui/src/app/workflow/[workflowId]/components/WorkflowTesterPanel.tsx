"use client";

import { Loader2, MessageSquareText, Mic, Phone, RefreshCw, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { createWorkflowRunApiV1WorkflowWorkflowIdRunsPost } from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { WORKFLOW_RUN_MODES } from "@/constants/workflowRunModes";
import { useAuth } from "@/lib/auth";
import { cn, getRandomId } from "@/lib/utils";

import { AiSimulatorPlaceholder } from "./workflow-tester/AiSimulatorPlaceholder";
import { EmbeddedVoiceTester } from "./workflow-tester/EmbeddedVoiceTester";
import { ManualTextChatPanel } from "./workflow-tester/ManualTextChatPanel";
import { ChatModeToggle, DisabledNotice, EmptyState } from "./workflow-tester/shared";
import { extractSdkErrorMessage, getErrorMessage } from "./workflow-tester/utils";

interface WorkflowTesterPanelProps {
    workflowId: number;
    initialContextVariables?: Record<string, string>;
    disabled: boolean;
    disabledReason: string | null;
    className?: string;
    onClose?: () => void;
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
                            <DisabledNotice
                                reason={authUnavailableReason ?? "Authentication is required before browser tests can start."}
                            />
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
                                    description="Test the agent over a voice call. Some telephony-only tools, like call transfer, are not yet supported here."
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
                                    onClick={() => setChatSessionKey((value) => value + 1)}
                                    disabled={testerBlocked}
                                    className="h-7 px-2 text-xs text-muted-foreground hover:text-foreground"
                                >
                                    <RefreshCw className="h-3.5 w-3.5" />
                                    Reset
                                </Button>
                            ) : null}
                        </div>

                        {chatMode === "manual" ? (
                            <ManualTextChatPanel
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
