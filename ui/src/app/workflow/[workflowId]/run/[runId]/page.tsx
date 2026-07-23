'use client';

import {
    Bot,
    Check,
    Clock,
    Copy,
    Download,
    ExternalLink,
    FileText,
    Loader2,
    Pause,
    Play,
    UserRound,
    Video,
} from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import posthog from 'posthog-js';
import { useEffect, useRef, useState } from 'react';

import WorkflowLayout from '@/app/workflow/WorkflowLayout';
import {
    getWorkflowApiV1WorkflowFetchWorkflowIdGet,
    getWorkflowRunApiV1WorkflowWorkflowIdRunsRunIdGet,
} from '@/client/sdk.gen';
import { MediaPreviewButton, MediaPreviewDialog } from '@/components/MediaPreviewDialog';
import { OnboardingTooltip } from '@/components/onboarding/OnboardingTooltip';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { ConversationRailFrame, RealtimeFeedback, WorkflowRunLogs } from '@/components/workflow/conversation';
import { PostHogEvent } from '@/constants/posthog-events';
import { WORKFLOW_RUN_MODES } from '@/constants/workflowRunModes';
import { useOrganizationTimezone } from '@/hooks/useOrganizationTimezone';
import { useAuth } from '@/lib/auth';
import { formatDateTime } from '@/lib/dateTime';
import { downloadFile, getSignedUrl } from '@/lib/files';
import { cn } from '@/lib/utils';

interface WorkflowRunResponse {
    mode: string;
    created_at: string | null;
    is_completed: boolean;
    transcript_url: string | null;
    recording_url: string | null;
    user_recording_url: string | null;
    bot_recording_url: string | null;
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
const WAVEFORM_BAR_COUNT = 96;
type SplitTrackPlaybackMode = 'both' | 'user' | 'bot';

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

function CopyDebugIdButton({ label, value }: { label: string; value: string }) {
    const [copied, setCopied] = useState(false);

    const handleCopy = async () => {
        await navigator.clipboard.writeText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <div className="flex items-center gap-2 rounded-md border border-border bg-muted/30 px-3 py-2">
            <div className="min-w-0">
                <p className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">{label}</p>
                <p className="font-mono text-sm font-semibold text-foreground">{value}</p>
            </div>
            <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-7 w-7 shrink-0"
                onClick={handleCopy}
                aria-label={`Copy ${label.toLowerCase()}`}
            >
                {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            </Button>
        </div>
    );
}

function buildWaveformPeaks(audioBuffer: AudioBuffer) {
    const channel = audioBuffer.getChannelData(0);
    const samplesPerBar = Math.max(1, Math.floor(channel.length / WAVEFORM_BAR_COUNT));

    return Array.from({ length: WAVEFORM_BAR_COUNT }, (_, index) => {
        const start = index * samplesPerBar;
        const end = Math.min(start + samplesPerBar, channel.length);
        let sum = 0;

        for (let i = start; i < end; i += 1) {
            sum += channel[i] * channel[i];
        }

        const rms = Math.sqrt(sum / Math.max(1, end - start));
        return Math.max(0.08, Math.min(1, rms * 5));
    });
}

async function loadWaveformPeaks(url: string) {
    const response = await fetch(url);
    const audioData = await response.arrayBuffer();
    const AudioContextConstructor =
        window.AudioContext ||
        (window as typeof window & { webkitAudioContext?: typeof AudioContext })
            .webkitAudioContext;

    if (!AudioContextConstructor) return null;

    const audioContext = new AudioContextConstructor();
    try {
        const decoded = await audioContext.decodeAudioData(audioData);
        return buildWaveformPeaks(decoded);
    } finally {
        void audioContext.close();
    }
}

function getAudioDuration(audio: HTMLAudioElement | null) {
    return audio && Number.isFinite(audio.duration) ? audio.duration : 0;
}

function getAudioTimelineState(audios: HTMLAudioElement[]) {
    const duration = Math.max(0, ...audios.map((audio) => getAudioDuration(audio)));
    const currentTime = Math.max(0, ...audios.map((audio) => audio.currentTime));

    return { duration, currentTime };
}

function syncAudioCurrentTime(audio: HTMLAudioElement, startTime: number) {
    const duration = getAudioDuration(audio);
    audio.currentTime = Math.min(startTime, duration || startTime);
}

function WaveformLane({
    peaks,
    track,
    position,
    isActive,
}: {
    peaks: number[] | null;
    track: 'user' | 'bot';
    position: 'top' | 'bottom';
    isActive: boolean;
}) {
    return (
        <div
            className={cn(
                'absolute left-3 right-3 flex gap-0.5',
                isActive ? 'opacity-85' : 'opacity-25',
                position === 'top' ? 'top-5 h-12 items-end' : 'bottom-5 h-12 items-start'
            )}
        >
            {peaks ? (
                peaks.map((peak, index) => (
                    <span
                        key={`${track}-${index}`}
                        className={cn(
                            'min-h-1 flex-1 rounded-full',
                            track === 'user' ? 'bg-sky-500' : 'bg-emerald-500'
                        )}
                        style={{ height: `${Math.round(peak * 100)}%` }}
                    />
                ))
            ) : (
                <div className="my-auto h-px w-full bg-border" />
            )}
        </div>
    );
}

function SplitTracksSection({
    userRecordingUrl,
    botRecordingUrl,
}: {
    userRecordingUrl: string;
    botRecordingUrl: string;
}) {
    const userAudioRef = useRef<HTMLAudioElement | null>(null);
    const botAudioRef = useRef<HTMLAudioElement | null>(null);
    const [signedUrls, setSignedUrls] = useState<{ user: string | null; bot: string | null }>({
        user: null,
        bot: null,
    });
    const [peaks, setPeaks] = useState<{ user: number[] | null; bot: number[] | null }>({
        user: null,
        bot: null,
    });
    const [isLoading, setIsLoading] = useState(false);
    const [isPlaying, setIsPlaying] = useState(false);
    const [progress, setProgress] = useState(0);
    const [playbackMode, setPlaybackMode] = useState<SplitTrackPlaybackMode>('both');

    const getPlaybackAudios = (mode: SplitTrackPlaybackMode) => {
        const audios: HTMLAudioElement[] = [];

        if (mode !== 'bot' && userAudioRef.current) {
            audios.push(userAudioRef.current);
        }

        if (mode !== 'user' && botAudioRef.current) {
            audios.push(botAudioRef.current);
        }

        return audios;
    };

    useEffect(() => {
        let isActive = true;
        const userAudio = userAudioRef.current;
        const botAudio = botAudioRef.current;

        userAudio?.pause();
        botAudio?.pause();
        setSignedUrls({ user: null, bot: null });
        setPeaks({ user: null, bot: null });
        setIsPlaying(false);
        setProgress(0);
        setPlaybackMode('both');
        setIsLoading(true);

        async function loadTracks() {
            try {
                const [userUrl, botUrl] = await Promise.all([
                    getSignedUrl(userRecordingUrl, true),
                    getSignedUrl(botRecordingUrl, true),
                ]);
                if (!isActive) return;

                setSignedUrls({ user: userUrl, bot: botUrl });
                if (!userUrl || !botUrl) return;

                const [userPeaks, botPeaks] = await Promise.all([
                    loadWaveformPeaks(userUrl),
                    loadWaveformPeaks(botUrl),
                ]);

                if (isActive) {
                    setPeaks({ user: userPeaks, bot: botPeaks });
                }
            } catch (error) {
                console.error('Error loading split track waveforms:', error);
            } finally {
                if (isActive) {
                    setIsLoading(false);
                }
            }
        }

        void loadTracks();

        return () => {
            isActive = false;
            userAudio?.pause();
            botAudio?.pause();
        };
    }, [userRecordingUrl, botRecordingUrl]);

    useEffect(() => {
        if (!isPlaying) return;

        let frameId: number;
        const updateProgress = () => {
            const activeAudios: HTMLAudioElement[] = [];

            if (playbackMode !== 'bot' && userAudioRef.current) {
                activeAudios.push(userAudioRef.current);
            }

            if (playbackMode !== 'user' && botAudioRef.current) {
                activeAudios.push(botAudioRef.current);
            }

            const { duration, currentTime } = getAudioTimelineState(activeAudios);

            setProgress(duration > 0 ? Math.min(1, currentTime / duration) : 0);
            frameId = window.requestAnimationFrame(updateProgress);
        };

        frameId = window.requestAnimationFrame(updateProgress);
        return () => window.cancelAnimationFrame(frameId);
    }, [isPlaying, playbackMode]);

    const pauseTracks = () => {
        userAudioRef.current?.pause();
        botAudioRef.current?.pause();
        setIsPlaying(false);
    };

    const handleTrackEnded = () => {
        const activeAudios = getPlaybackAudios(playbackMode);
        const activeTracksDone = activeAudios.length > 0 && activeAudios.every((audio) => audio.ended);

        if (activeTracksDone) {
            setIsPlaying(false);
            setProgress(1);
        }
    };

    const handlePlaybackModeChange = async (nextMode: SplitTrackPlaybackMode) => {
        if (nextMode === playbackMode) return;

        const { currentTime } = getAudioTimelineState(getPlaybackAudios(playbackMode));
        const nextAudios = getPlaybackAudios(nextMode);
        const { duration } = getAudioTimelineState(nextAudios);
        const startTime = duration > 0 && currentTime >= duration - 0.1 ? 0 : currentTime;

        userAudioRef.current?.pause();
        botAudioRef.current?.pause();
        nextAudios.forEach((audio) => syncAudioCurrentTime(audio, startTime));
        setPlaybackMode(nextMode);
        setProgress(duration > 0 ? Math.min(1, startTime / duration) : 0);

        if (!isPlaying) return;

        if (nextAudios.length === 0) {
            setIsPlaying(false);
            return;
        }

        try {
            await Promise.all(nextAudios.map((audio) => audio.play()));
            setIsPlaying(true);
        } catch (error) {
            pauseTracks();
            console.error('Error switching split track playback:', error);
        }
    };

    const handleTrackButtonClick = (track: 'user' | 'bot') => {
        const nextMode = playbackMode === track ? 'both' : track;
        void handlePlaybackModeChange(nextMode);
    };

    const togglePlayback = async () => {
        const playbackAudios = getPlaybackAudios(playbackMode);
        if (!canPlay || playbackAudios.length === 0) return;

        if (isPlaying) {
            pauseTracks();
            return;
        }

        const { duration, currentTime } = getAudioTimelineState(playbackAudios);
        const startTime = duration > 0 && currentTime >= duration - 0.1 ? 0 : currentTime;

        userAudioRef.current?.pause();
        botAudioRef.current?.pause();
        playbackAudios.forEach((audio) => syncAudioCurrentTime(audio, startTime));

        try {
            await Promise.all(playbackAudios.map((audio) => audio.play()));
            setIsPlaying(true);
        } catch (error) {
            pauseTracks();
            console.error('Error playing split tracks:', error);
        }
    };

    const canPlay =
        playbackMode === 'both'
            ? Boolean(signedUrls.user && signedUrls.bot)
            : playbackMode === 'user'
                ? Boolean(signedUrls.user)
                : Boolean(signedUrls.bot);
    const progressPercent = Math.round(progress * 1000) / 10;
    const userTrackActive = playbackMode !== 'bot';
    const botTrackActive = playbackMode !== 'user';
    const playbackTargetLabel = playbackMode === 'both' ? 'split tracks' : `${playbackMode} track`;

    return (
        <Card className="border-border">
            <audio
                ref={userAudioRef}
                src={signedUrls.user ?? undefined}
                preload="metadata"
                className="hidden"
                onEnded={handleTrackEnded}
            />
            <audio
                ref={botAudioRef}
                src={signedUrls.bot ?? undefined}
                preload="metadata"
                className="hidden"
                onEnded={handleTrackEnded}
            />
            <CardHeader className="pb-3">
                <CardTitle className="text-lg">Split Tracks</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-2" role="group" aria-label="Playback tracks">
                        <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            aria-pressed={userTrackActive}
                            aria-label={playbackMode === 'user' ? 'Play both tracks' : 'Play user track only'}
                            onClick={() => handleTrackButtonClick('user')}
                            className={cn(
                                'gap-1.5',
                                userTrackActive
                                    ? 'border-sky-200 bg-sky-50 text-sky-700 hover:bg-sky-100 dark:border-sky-900/60 dark:bg-sky-950/30 dark:text-sky-300'
                                    : 'text-muted-foreground opacity-60'
                            )}
                        >
                            <UserRound className="h-4 w-4" />
                            User
                        </Button>
                        <span className="h-4 w-px bg-border" />
                        <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            aria-pressed={botTrackActive}
                            aria-label={playbackMode === 'bot' ? 'Play both tracks' : 'Play bot track only'}
                            onClick={() => handleTrackButtonClick('bot')}
                            className={cn(
                                'gap-1.5',
                                botTrackActive
                                    ? 'border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 dark:border-emerald-900/60 dark:bg-emerald-950/30 dark:text-emerald-300'
                                    : 'text-muted-foreground opacity-60'
                            )}
                        >
                            <Bot className="h-4 w-4" />
                            Bot
                        </Button>
                    </div>
                    <div className="flex items-center gap-2">
                        <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => downloadFile(userRecordingUrl)}
                            className="gap-2"
                        >
                            <Download className="h-4 w-4" />
                            User
                        </Button>
                        <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => downloadFile(botRecordingUrl)}
                            className="gap-2"
                        >
                            <Download className="h-4 w-4" />
                            Bot
                        </Button>
                    </div>
                </div>
                <div className="flex items-center gap-4">
                    <Button
                        type="button"
                        size="icon"
                        variant={isPlaying ? 'default' : 'outline'}
                        onClick={togglePlayback}
                        disabled={!canPlay}
                        aria-label={isPlaying ? `Pause ${playbackTargetLabel}` : `Play ${playbackTargetLabel}`}
                        className="h-10 w-10 shrink-0"
                    >
                        {isPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                    </Button>
                    <div className="relative h-36 min-w-0 flex-1 overflow-hidden rounded-lg border border-border/70 bg-background">
                        <div className="absolute left-3 right-3 top-1/2 h-px bg-border/80" />
                        <WaveformLane peaks={peaks.user} track="user" position="top" isActive={userTrackActive} />
                        <WaveformLane peaks={peaks.bot} track="bot" position="bottom" isActive={botTrackActive} />
                        {canPlay && (
                            <div className="pointer-events-none absolute inset-x-3 inset-y-3">
                                <div
                                    className="absolute top-0 bottom-0 w-px bg-foreground/50"
                                    style={{ left: `${progressPercent}%` }}
                                />
                            </div>
                        )}
                        {isLoading && (
                            <div className="absolute inset-0 flex items-center justify-center bg-background/70 text-xs text-muted-foreground">
                                <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                                Loading
                            </div>
                        )}
                    </div>
                </div>
            </CardContent>
        </Card>
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
    const organizationTimezone = useOrganizationTimezone();
    const [workflowRun, setWorkflowRun] = useState<WorkflowRunResponse | null>(null);
    const [workflowName, setWorkflowName] = useState<string | null>(null);
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
            setWorkflowName(null);
            const workflowId = Number(params.workflowId);
            const runId = Number(params.runId);

            try {
                const [runResponse, workflowResponse] = await Promise.all([
                    getWorkflowRunApiV1WorkflowWorkflowIdRunsRunIdGet({
                        path: {
                            workflow_id: workflowId,
                            run_id: runId,
                        },
                    }),
                    getWorkflowApiV1WorkflowFetchWorkflowIdGet({
                        path: {
                            workflow_id: workflowId,
                        },
                    }),
                ]);

                setWorkflowName(workflowResponse.data?.name ?? null);
                const runData = {
                    mode: runResponse.data?.mode ?? '',
                    created_at: runResponse.data?.created_at ?? null,
                    is_completed: runResponse.data?.is_completed ?? false,
                    transcript_url: runResponse.data?.transcript_url ?? null,
                    recording_url: runResponse.data?.recording_url ?? null,
                    user_recording_url: runResponse.data?.user_recording_url ?? null,
                    bot_recording_url: runResponse.data?.bot_recording_url ?? null,
                    cost_info: runResponse.data?.cost_info ?? null,
                    initial_context: runResponse.data?.initial_context as Record<string, string> | null ?? null,
                    gathered_context: runResponse.data?.gathered_context as Record<string, string> | null ?? null,
                    logs: runResponse.data?.logs as WorkflowRunLogs | null ?? null,
                    annotations: runResponse.data?.annotations as Record<string, unknown> | null ?? null,
                };
                setWorkflowRun(runData);
                posthog.capture(PostHogEvent.WORKFLOW_RUN_DETAILS_VIEWED, {
                    workflow_id: workflowId,
                    workflow_name: workflowResponse.data?.name ?? null,
                    run_id: runId,
                    is_completed: runData.is_completed,
                    has_recording: !!runData.recording_url,
                    has_split_recordings: !!runData.user_recording_url && !!runData.bot_recording_url,
                    has_transcript: !!runData.transcript_url,
                });
            } finally {
                setIsLoading(false);
            }
        };
        fetchWorkflowRun();
    }, [params.workflowId, params.runId, auth]);

    let returnValue = null;
    const isTextChatRun = workflowRun?.mode === WORKFLOW_RUN_MODES.TEXTCHAT;
    const showRunDetailsView = Boolean(workflowRun?.is_completed || isTextChatRun);
    const userSplitRecordingUrl = workflowRun?.user_recording_url ?? null;
    const botSplitRecordingUrl = workflowRun?.bot_recording_url ?? null;
    const hasSplitTracks = Boolean(userSplitRecordingUrl && botSplitRecordingUrl);
    const workflowId = String(params.workflowId);
    const runId = String(params.runId);

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
                        <CardHeader className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                            <div className="min-w-0 flex-1 space-y-2">
                                {workflowName && (
                                    <div className="flex min-w-0 items-center gap-3">
                                        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-primary/20 bg-primary/10 text-primary">
                                            <Bot className="h-5 w-5" />
                                        </div>
                                        <div className="min-w-0">
                                            <p className="text-xs font-medium uppercase tracking-[0.14em] text-muted-foreground">
                                                Agent
                                            </p>
                                            <p className="truncate text-xl font-semibold text-foreground">
                                                {workflowName}
                                            </p>
                                        </div>
                                    </div>
                                )}
                                <div className="flex flex-wrap gap-2 pt-1">
                                    <CopyDebugIdButton label="Agent ID" value={workflowId} />
                                    <CopyDebugIdButton label="Run ID" value={runId} />
                                </div>
                                <div className="flex min-w-0 items-center gap-4 pt-1">
                                    <CardTitle className="min-w-0 text-2xl">
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
                                {workflowRun?.created_at && (
                                    <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
                                        <Clock className="h-4 w-4" />
                                        Call time: {formatDateTime(workflowRun.created_at, organizationTimezone)}
                                    </p>
                                )}
                            </div>
                            <div className="flex shrink-0 items-center gap-2">
                                <Link href={`/workflow/${params.workflowId}`}>
                                    <Button
                                        ref={customizeButtonRef}
                                        className="gap-2"
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

                        {!isTextChatRun && hasSplitTracks && (
                            <SplitTracksSection
                                userRecordingUrl={userSplitRecordingUrl as string}
                                botRecordingUrl={botSplitRecordingUrl as string}
                            />
                        )}

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
                    tooltipKey="customize_workflow"
                    title='Customize Your Workflow'
                    targetRef={customizeButtonRef}
                    message="Edit your workflow to adjust the voice agent's behavior, add new steps, or modify the conversation flow."
                    showNext={false}
                />
            )}
        </WorkflowLayout>
    );
}
