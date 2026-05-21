'use client';

import { Check, Copy, ExternalLink, FileText, Video } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import posthog from 'posthog-js';
import { useEffect, useRef, useState } from 'react';

import WorkflowLayout from '@/app/workflow/WorkflowLayout';
import { getWorkflowRunApiV1WorkflowWorkflowIdRunsRunIdGet } from '@/client/sdk.gen';
import { MediaPreviewButton, MediaPreviewDialog } from '@/components/MediaPreviewDialog';
import { OnboardingTooltip } from '@/components/onboarding/OnboardingTooltip';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { ConversationRailFrame, RealtimeFeedback, WorkflowRunLogs } from '@/components/workflow/conversation';
import { PostHogEvent } from '@/constants/posthog-events';
import { WORKFLOW_RUN_MODES } from '@/constants/workflowRunModes';
import { useOnboarding } from '@/context/OnboardingContext';
import { useAuth } from '@/lib/auth';
import { downloadFile } from '@/lib/files';

interface WorkflowRunResponse {
    mode: string;
    is_completed: boolean;
    transcript_url: string | null;
    recording_url: string | null;
    cost_info: {
        dograh_token_usage?: number | null;
        call_duration_seconds?: number | null;
    } | null;
    initial_context: Record<string, string | number | boolean | object> | null;
    gathered_context: Record<string, string | number | boolean | object> | null;
    logs: WorkflowRunLogs | null;
    annotations: Record<string, unknown> | null;
}

const RUN_SHELL_HEIGHT_CLASS = "h-[calc(100svh-49px)] min-h-[calc(100svh-49px)] max-h-[calc(100svh-49px)]";

function formatDuration(seconds?: number | null) {
    if (seconds == null || Number.isNaN(seconds)) return 'N/A';
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    if (mins === 0) return `${secs}s`;
    return `${mins}m ${secs}s`;
}

function getTranscriptMetrics(logs: WorkflowRunLogs | null, gatheredContext: Record<string, string | number | boolean | object> | null) {
    const events = logs?.realtime_feedback_events ?? [];
    const userTurns = events.filter((event) => event.type === 'rtf-user-transcription' && event.payload.final).length;
    const botTurns = events.filter((event) => event.type === 'rtf-bot-text').length;
    const toolCalls = events.filter((event) => event.type === 'rtf-function-call-end').length;
    const nodeNames = new Set(
        events
            .map((event) => event.payload.node_name)
            .filter((nodeName): nodeName is string => Boolean(nodeName))
    );
    const visitedNodes = Array.isArray(gatheredContext?.nodes_visited)
        ? gatheredContext.nodes_visited.length
        : nodeNames.size;

    return { userTurns, botTurns, toolCalls, visitedNodes };
}

function MetricCard({ label, value }: { label: string; value: string }) {
    return (
        <div className="rounded-xl border border-border bg-muted/40 px-4 py-3">
            <p className="text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">{label}</p>
            <p className="mt-2 text-lg font-semibold text-foreground">{value}</p>
        </div>
    );
}

function RunMetricsSection({
    costInfo,
    logs,
    gatheredContext,
}: {
    costInfo: WorkflowRunResponse['cost_info'];
    logs: WorkflowRunLogs | null;
    gatheredContext: Record<string, string | number | boolean | object> | null;
}) {
    const metrics = getTranscriptMetrics(logs, gatheredContext);

    return (
        <Card className="border-border">
            <CardHeader className="pb-3">
                <CardTitle className="text-lg">Run Metrics</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                <MetricCard label="Duration" value={formatDuration(costInfo?.call_duration_seconds)} />
                <MetricCard
                    label="Token Usage"
                    value={costInfo?.dograh_token_usage != null ? costInfo.dograh_token_usage.toLocaleString() : 'N/A'}
                />
                <MetricCard label="User Turns" value={String(metrics.userTurns)} />
                <MetricCard label="Bot Turns" value={String(metrics.botTurns)} />
                <MetricCard label="Tool Calls" value={String(metrics.toolCalls)} />
                <MetricCard label="Nodes Visited" value={String(metrics.visitedNodes)} />
            </CardContent>
        </Card>
    );
}

function ContextDisplay({ title, context }: { title: string; context: Record<string, string | number | boolean | object> | null }) {
    const [copied, setCopied] = useState(false);

    const handleCopy = () => {
        if (!context) return;
        navigator.clipboard.writeText(JSON.stringify(context, null, 2));
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    if (!context || Object.keys(context).length === 0) {
        return (
            <Card className="border-border">
                <CardHeader className="pb-2">
                    <CardTitle className="text-lg">{title}</CardTitle>
                </CardHeader>
                <CardContent>
                    <p className="text-sm text-muted-foreground">No data available</p>
                </CardContent>
            </Card>
        );
    }

    return (
        <Card className="border-border">
            <CardHeader className="flex flex-row items-center justify-between pb-2">
                <CardTitle className="text-lg">{title}</CardTitle>
                <Button variant="ghost" size="sm" onClick={handleCopy} className="gap-2">
                    {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                    {copied ? 'Copied' : 'Copy'}
                </Button>
            </CardHeader>
            <CardContent>
                <pre className="text-sm bg-muted p-3 rounded-md overflow-auto max-h-64">
                    {JSON.stringify(context, null, 2)}
                </pre>
            </CardContent>
        </Card>
    );
}


export default function WorkflowRunPage() {
    const params = useParams();
    const [isLoading, setIsLoading] = useState(true);
    const auth = useAuth();
    const [workflowRun, setWorkflowRun] = useState<WorkflowRunResponse | null>(null);
    const { hasSeenTooltip, markTooltipSeen } = useOnboarding();
    const customizeButtonRef = useRef<HTMLButtonElement>(null);

    // Redirect if not authenticated
    useEffect(() => {
        if (!auth.loading && !auth.isAuthenticated) {
            auth.redirectToLogin();
        }
    }, [auth]);

    const { openPreview, dialog } = MediaPreviewDialog();

    useEffect(() => {
        const fetchWorkflowRun = async () => {
            if (!auth.isAuthenticated || auth.loading) return;

            setIsLoading(true);
            const workflowId = params.workflowId;
            const runId = params.runId;
            const response = await getWorkflowRunApiV1WorkflowWorkflowIdRunsRunIdGet({
                path: {
                    workflow_id: Number(workflowId),
                    run_id: Number(runId),
                },
            });
            setIsLoading(false);
            const runData = {
                mode: response.data?.mode ?? '',
                is_completed: response.data?.is_completed ?? false,
                transcript_url: response.data?.transcript_url ?? null,
                recording_url: response.data?.recording_url ?? null,
                cost_info: response.data?.cost_info ?? null,
                initial_context: response.data?.initial_context as Record<string, string> | null ?? null,
                gathered_context: response.data?.gathered_context as Record<string, string> | null ?? null,
                logs: response.data?.logs as WorkflowRunLogs | null ?? null,
                annotations: response.data?.annotations as Record<string, unknown> | null ?? null,
            };
            setWorkflowRun(runData);
            posthog.capture(PostHogEvent.WORKFLOW_RUN_DETAILS_VIEWED, {
                workflow_id: Number(workflowId),
                run_id: Number(runId),
                is_completed: runData.is_completed,
                has_recording: !!runData.recording_url,
                has_transcript: !!runData.transcript_url,
            });
        };
        fetchWorkflowRun();
    }, [params.workflowId, params.runId, auth]);

    let returnValue = null;
    const isTextChatRun = workflowRun?.mode === WORKFLOW_RUN_MODES.TEXTCHAT;
    const showRunDetailsView = Boolean(workflowRun?.is_completed || isTextChatRun);

    if (isLoading) {
        returnValue = (
            <div className="h-full flex items-center justify-center">
                <div className="w-full max-w-4xl p-6">
                    <Card>
                        <CardHeader>
                            <Skeleton className="h-6 w-48" />
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <Skeleton className="h-4 w-full" />
                            <Skeleton className="h-4 w-3/4" />
                            <Skeleton className="h-4 w-1/2" />
                        </CardContent>
                        <CardFooter className="flex gap-4">
                            <Skeleton className="h-10 w-32" />
                            <Skeleton className="h-10 w-32" />
                        </CardFooter>
                    </Card>
                </div>
            </div>
        );
    }
    else if (showRunDetailsView) {
        returnValue = (
            <div className={`flex ${RUN_SHELL_HEIGHT_CLASS} min-h-0 w-full overflow-hidden bg-background`}>
                <div className="min-w-0 flex-1 overflow-y-auto">
                    <div className="mx-auto w-full max-w-4xl space-y-6 p-6">
                    <Card className="border-border">
                        <CardHeader className="flex flex-row items-center justify-between">
                            <div className="flex items-center gap-4">
                                <CardTitle className="text-2xl">
                                    {isTextChatRun ? 'Text Chat Session' : 'Agent Run Completed'}
                                </CardTitle>
                                <div className={`h-8 w-8 rounded-full flex items-center justify-center ${isTextChatRun ? 'bg-sky-500/15' : 'bg-emerald-500/20'}`}>
                                    {isTextChatRun ? (
                                        <FileText className="h-5 w-5 text-sky-500" />
                                    ) : (
                                        <svg className="h-5 w-5 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
                                        </svg>
                                    )}
                                </div>
                            </div>
                            <div className="flex items-center gap-2">
                                <Link href={`/workflow/${params.workflowId}`}>
                                    <Button
                                        ref={customizeButtonRef}
                                        className="gap-2"
                                        onClick={() => {
                                            if (!hasSeenTooltip('customize_workflow')) {
                                                markTooltipSeen('customize_workflow');
                                            }
                                        }}
                                    >
                                        <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                                        </svg>
                                        Customize Agent
                                    </Button>
                                </Link>
                            </div>
                        </CardHeader>
                        <CardContent>
                            <p className="text-muted-foreground mb-8">
                                {isTextChatRun
                                    ? 'Review the conversation history, metrics, and context captured for this text session.'
                                    : 'Your voice agent run has been completed successfully. You can preview or download the transcript and recording.'}
                            </p>

                            <div className="flex flex-wrap gap-4">
                                {!isTextChatRun && (
                                    <>
                                        <div className="flex items-center gap-2">
                                            <span className="text-sm text-muted-foreground">Preview:</span>
                                            <MediaPreviewButton
                                                recordingUrl={workflowRun?.recording_url}
                                                transcriptUrl={workflowRun?.transcript_url}
                                                runId={Number(params.runId)}
                                                onOpenPreview={openPreview}
                                            />
                                        </div>
                                        <div className="flex items-center gap-2 border-l border-border pl-4">
                                            <span className="text-sm text-muted-foreground">Download:</span>
                                            <Button
                                                onClick={() => downloadFile(workflowRun?.transcript_url ?? null)}
                                                disabled={!workflowRun?.transcript_url || !auth.isAuthenticated}
                                                size="sm"
                                                className="gap-2"
                                            >
                                                <FileText className="h-4 w-4" />
                                                Transcript
                                            </Button>
                                            <Button
                                                onClick={() => downloadFile(workflowRun?.recording_url ?? null)}
                                                disabled={!workflowRun?.recording_url || !auth.isAuthenticated}
                                                size="sm"
                                                className="gap-2"
                                            >
                                                <Video className="h-4 w-4" />
                                                Recording
                                            </Button>
                                        </div>
                                    </>
                                )}
                                {workflowRun?.gathered_context?.trace_url && (
                                    <div className={`flex items-center gap-2 ${isTextChatRun ? '' : 'border-l border-border pl-4'}`}>
                                        <span className="text-sm text-muted-foreground">Trace:</span>
                                        <Button
                                            asChild
                                            size="sm"
                                            variant="outline"
                                            className="gap-2"
                                        >
                                            <a
                                                href={String(workflowRun.gathered_context.trace_url)}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                            >
                                                <ExternalLink className="h-4 w-4" />
                                                View Trace
                                            </a>
                                        </Button>
                                    </div>
                                )}
                            </div>
                        </CardContent>
                    </Card>

                        <RunMetricsSection
                            costInfo={workflowRun?.cost_info ?? null}
                            logs={workflowRun?.logs ?? null}
                            gatheredContext={workflowRun?.gathered_context ?? null}
                        />

                        <div className="grid gap-6 md:grid-cols-2">
                            <ContextDisplay
                                title="Initial Context"
                                context={workflowRun?.initial_context ?? null}
                            />
                            <ContextDisplay
                                title="Gathered Context"
                                context={workflowRun?.gathered_context ?? null}
                            />
                        </div>

                        {workflowRun?.annotations && Object.keys(workflowRun.annotations).length > 0 && (
                            <ContextDisplay
                                title="QA Results"
                                context={workflowRun.annotations as Record<string, string | number | boolean | object>}
                            />
                        )}
                    </div>
                </div>

                <div className="h-full min-h-0 w-[420px] shrink-0 border-l border-border bg-background p-5">
                    <ConversationRailFrame className="h-full">
                        <RealtimeFeedback mode="historical" logs={workflowRun?.logs ?? null} />
                    </ConversationRailFrame>
                </div>
            </div>
        );
    }
    else {
        returnValue = (
            <div className="flex h-full items-center justify-center p-6">
                <Card className="w-full max-w-xl border-border">
                    <CardHeader className="space-y-2">
                        <CardTitle className="text-2xl">Run Details Unavailable</CardTitle>
                        <p className="text-sm text-muted-foreground">
                            This run does not have a details view yet. Go back to the workflow to continue testing or make changes.
                        </p>
                    </CardHeader>
                    <CardFooter>
                        <Button asChild className="gap-2">
                            <Link href={`/workflow/${params.workflowId}`}>
                                Customize Agent
                            </Link>
                        </Button>
                    </CardFooter>
                </Card>
            </div>
        );
    }

    return (
        <WorkflowLayout>
            {returnValue}
            {dialog}

            {/* Onboarding Tooltip for Customize Workflow */}
            {showRunDetailsView && (
                <OnboardingTooltip
                    title='Customize Your Workflow'
                    targetRef={customizeButtonRef}
                    message="Edit your workflow to adjust the voice agent's behavior, add new steps, or modify the conversation flow."
                    onDismiss={() => markTooltipSeen('customize_workflow')}
                    showNext={false}
                    isVisible={!hasSeenTooltip('customize_workflow')}
                />
            )}
        </WorkflowLayout>
    );
}
