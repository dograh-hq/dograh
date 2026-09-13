"use client";

import { Check, Code2, Loader2, Phone, RefreshCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { createOrUpdateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenPost } from "@/client/sdk.gen";
import { SpatialAvatarPanel } from "@/components/avatar/SpatialAvatarPanel";
import { Button } from "@/components/ui/button";
import { RealtimeFeedback } from "@/components/workflow/conversation";
import { copyTextToClipboard } from "@/lib/clipboard";

import { ApiKeyErrorDialog, ConnectionStatus, WorkflowConfigErrorDialog } from "../../run/[runId]/components";
import { useWebSocketRTC } from "../../run/[runId]/hooks";
import type { WorkflowRuntimeNodeTransition } from "./types";

interface EmbeddedVoiceTesterProps {
    workflowId: number;
    workflowRunId: number;
    initialContextVariables?: Record<string, string>;
    accessToken: string;
    onReset: () => void;
    onNodeTransition?: (transition: WorkflowRuntimeNodeTransition) => void;
}

export function EmbeddedVoiceTester({
    workflowId,
    workflowRunId,
    initialContextVariables,
    accessToken,
    onReset,
    onNodeTransition,
}: EmbeddedVoiceTesterProps) {
    const router = useRouter();
    const [botSpeaking, setBotSpeaking] = useState(false);
    const handleBotSpeakingChange = useCallback((speaking: boolean) => {
        setBotSpeaking(speaking);
    }, []);
    // Avatar gating: 'unknown' until the avatar panel resolves config;
    // 'drives' means the avatar owns audio and starts the call via its own
    // gesture (suppress auto-start); 'none' means audio-only (auto-start).
    const [avatarGate, setAvatarGate] = useState<'unknown' | 'drives' | 'none'>('unknown');
    const handleAvatarWillDrive = useCallback((willDrive: boolean) => {
        setAvatarGate(willDrive ? 'drives' : 'none');
    }, []);
    const [iframeCopied, setIframeCopied] = useState(false);
    const [iframeLoading, setIframeLoading] = useState(false);
    const handleCopyIframe = useCallback(async () => {
        setIframeLoading(true);
        try {
            // Ensure the workflow has an embed token, then build the iframe
            // snippet from the deployed origin so it can be pasted anywhere.
            const res = await createOrUpdateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenPost({
                path: { workflow_id: workflowId },
                body: { settings: { widgetType: "voice" } },
            });
            if (res.error || !res.data?.token) {
                toast.error("Couldn't generate the embed token");
                return;
            }
            const origin = process.env.NEXT_PUBLIC_APP_URL || window.location.origin;
            const snippet =
                `<iframe\n` +
                `  src="${origin}/embed/avatar/${res.data.token}"\n` +
                `  allow="microphone; autoplay"\n` +
                `  width="400" height="620"\n` +
                `  style="border:0;border-radius:16px;max-width:100%">\n` +
                `</iframe>`;
            await copyTextToClipboard(snippet);
            setIframeCopied(true);
            toast.success("Avatar iframe copied — paste it into any HTTPS page");
            setTimeout(() => setIframeCopied(false), 2500);
        } catch {
            toast.error("Couldn't copy the iframe");
        } finally {
            setIframeLoading(false);
        }
    }, [workflowId]);
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
        appConfig,
        appConfigLoading,
        refreshAppConfig,
    } = useWebSocketRTC({
        workflowId,
        workflowRunId,
        accessToken,
        initialContextVariables,
        onNodeTransition,
        onBotSpeakingChange: handleBotSpeakingChange,
    });
    const autoStartedRef = useRef(false);
    const configRetriedRef = useRef(false);

    useEffect(() => {
        // Wait for appConfig (FORCE_TURN_RELAY) to finish loading before
        // auto-starting — this effect only ever fires start() once
        // (autoStartedRef), and createPeerConnection reads
        // appConfig?.forceTurnRelay synchronously, so a connection created
        // before that resolves permanently misses the relay-only
        // restriction for the whole call.
        if (autoStartedRef.current || appConfigLoading) {
            return;
        }

        // When the avatar drives this run it is the sole audio sink and starts
        // the call itself via its enable gesture — do not auto-start. Wait
        // until the avatar panel has resolved whether it will drive.
        if (avatarGate === 'unknown' || avatarGate === 'drives') {
            return;
        }

        // Loading having finished isn't enough by itself: /api/config/version
        // always resolves with HTTP 200 even when the backend healthcheck it
        // performs server-side failed or timed out, silently defaulting
        // forceTurnRelay to false in that response instead of reflecting the
        // deployment's real setting. Only a 'reachable' backendStatus
        // confirms forceTurnRelay actually came from the backend. Give it one
        // retry rather than either starting with an unconfirmed (possibly
        // wrong) value or waiting forever if the backend stays down.
        if (appConfig?.backendStatus !== "reachable") {
            if (!configRetriedRef.current) {
                configRetriedRef.current = true;
                void refreshAppConfig();
            }
            return;
        }

        autoStartedRef.current = true;
        void start();
    }, [start, appConfig?.backendStatus, appConfigLoading, refreshAppConfig, avatarGate]);

    // The avatar panel calls this once its enable gesture completes, so the
    // call starts with the avatar already listening (single start flow).
    const handleRequestCallStart = useCallback(() => {
        if (autoStartedRef.current) return;
        autoStartedRef.current = true;
        void start();
    }, [start]);

    // True once the one bounded retry above has run and the backend is
    // still unreachable — the auto-start effect deliberately gives up at
    // that point rather than starting with an unconfirmed forceTurnRelay
    // value, which would otherwise leave the tester stuck with no way to
    // ever start. Surface a manual retry instead.
    const configUnreachable =
        !appConfigLoading &&
        configRetriedRef.current &&
        appConfig?.backendStatus !== "reachable";

    const endButtonLabel = connectionActive
        ? "End Call"
        : isCompleted
            ? "Start Another Test"
            : connectionStatus === "failed"
                ? "Retry Call"
                : configUnreachable
                    ? "Retry Connection"
                    : "Starting Test...";

    const handleConfigRetry = () => {
        // Deliberately NOT resetting configRetriedRef here. It's already
        // true (that's the only way to reach configUnreachable), and the
        // auto-start effect's own retry gate reads it — resetting it would
        // make that effect think no retry has happened yet, so if this
        // manual refresh also comes back unreachable, the effect would fire
        // its own extra automatic refresh on top of this one (one click
        // producing two fetches instead of one). Leaving it true keeps that
        // budget spent; only this explicit call fetches.
        void refreshAppConfig();
    };

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
            return;
        }
        if (configUnreachable) {
            handleConfigRetry();
        }
    };

    return (
        <>
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border/70 bg-background">
                <div className="border-b border-border/70 p-3">
                    <SpatialAvatarPanel
                        accessToken={accessToken}
                        active={connectionActive}
                        botSpeaking={botSpeaking}
                        audioRef={audioRef}
                        workflowRunId={workflowRunId}
                        onAvatarWillDrive={handleAvatarWillDrive}
                        onRequestCallStart={handleRequestCallStart}
                    />
                    {avatarGate === 'drives' && (
                        <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            className="mt-2 w-full gap-2"
                            onClick={handleCopyIframe}
                            disabled={iframeLoading}
                        >
                            {iframeLoading ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : iframeCopied ? (
                                <Check className="h-4 w-4" />
                            ) : (
                                <Code2 className="h-4 w-4" />
                            )}
                            {iframeCopied ? "Copied!" : "Copy iframe"}
                        </Button>
                    )}
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
                            disabled={isStarting && connectionStatus !== "failed" && !configUnreachable}
                            variant={connectionActive ? "destructive" : "default"}
                            className="w-full"
                        >
                            {configUnreachable ? (
                                <>
                                    <RefreshCw className="h-4 w-4" />
                                    {endButtonLabel}
                                </>
                            ) : isStarting && connectionStatus !== "failed" ? (
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
                        {configUnreachable ? (
                            <p className="text-center text-sm text-muted-foreground">
                                Couldn&apos;t reach the backend to confirm call settings. Tap retry once it&apos;s back.
                            </p>
                        ) : null}
                    </div>
                </div>

                <audio ref={audioRef} autoPlay playsInline className="hidden" />
            </div>

            <ApiKeyErrorDialog
                open={apiKeyModalOpen}
                onOpenChange={setApiKeyModalOpen}
                error={apiKeyError}
                errorCode={apiKeyErrorCode}
                onNavigateToBilling={() => router.push("/billing")}
                onNavigateToDevelopers={() => router.push("/api-keys")}
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
