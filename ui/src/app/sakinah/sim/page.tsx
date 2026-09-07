"use client";

import { ChartBar, EyeOff, Loader2, Play, Square, Volume2, VolumeX } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import type { SimulationResponse } from "@/client";
import {
    startSimulationApiV1SakinahSimulationsPost,
    stopSimulationApiV1SakinahSimulationsSimulationIdStopPost,
} from "@/client";
import { client } from "@/client/client.gen";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { resolveBrowserBackendUrl } from "@/lib/apiClient";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { listSakinahScenarios } from "@/lib/sakinahPersistence";
import { compileScenarioPrompt } from "@/lib/sakinahScenarios";
import { cn } from "@/lib/utils";

import { SakinahRunHistory } from "../components/RunHistory";
import { CalmEvaluationPanel } from "./CalmEvaluationPanel";
import { type CalmAnalysis, CalmScoringPanel } from "./CalmScoringPanel";
import type { CalmEvaluationResult, TurnEvaluation } from "./calmTypes";

interface SimulationEvent {
    role: string;
    type: string;
    payload?: Record<string, unknown>;
    timestamp?: string;
}

interface SimTurn {
    id: string;
    role: "sakinah" | "service_user";
    text: string;
    final: boolean;
    timestamp?: string;
    evaluation?: TurnEvaluation;
}

type ExperimentMode = "baseline" | "scores_only" | "scores_and_trends" | "full_calm_prompt";
type SimulationAudioStatus = "idle" | "connecting" | "connected" | "error";

const ROLE_LABELS: Record<string, string> = {
    sakinah: "SAKINAH",
    service_user: "SERVICE USER",
};

export default function SakinahSimulationPage() {
    const { getAccessToken, isAuthenticated, loading: authLoading, redirectToLogin, user } = useAuth();
    const [scenario, setScenario] = useState("");
    const [scenarioId, setScenarioId] = useState<string | null>(null);
    const [scenarioName, setScenarioName] = useState<string | null>(null);
    const [simulation, setSimulation] = useState<SimulationResponse | null>(null);
    const [turns, setTurns] = useState<SimTurn[]>([]);
    const [starting, setStarting] = useState(false);
    const [stopping, setStopping] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [muted, setMuted] = useState(false);
    const [showScoringPanel, setShowScoringPanel] = useState(true);
    const [experimentMode, setExperimentMode] = useState<ExperimentMode>("full_calm_prompt");
    const [calmAnalysis, setCalmAnalysis] = useState<CalmAnalysis | null>(null);
    const [historyRefreshKey, setHistoryRefreshKey] = useState(0);
    const [audioStatus, setAudioStatus] = useState<SimulationAudioStatus>("idle");
    const [audioFrameCount, setAudioFrameCount] = useState(0);
    const wsRef = useRef<WebSocket | null>(null);
    const audioWsRef = useRef<WebSocket | null>(null);
    const audioCtxRef = useRef<AudioContext | null>(null);
    const gainRef = useRef<GainNode | null>(null);
    const audioKeepAliveRef = useRef<AudioScheduledSourceNode | null>(null);
    const nextPlayTimeRef = useRef(0);
    const transcriptRef = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        const scenarioId = new URLSearchParams(window.location.search).get("scenario");
        if (!scenarioId) return;
        if (authLoading) return;
        if (!user) {
            redirectToLogin();
            return;
        }
        void listSakinahScenarios()
            .then((savedScenarios) => {
                const savedScenario = savedScenarios.find((item) => item.id === scenarioId);
                if (savedScenario) {
                    setScenarioId(savedScenario.id);
                    setScenarioName(savedScenario.title);
                    setScenario(compileScenarioPrompt(savedScenario));
                }
            })
            .catch((loadError) => setError(loadError instanceof Error ? loadError.message : "Unable to load scenario."));
    }, [authLoading, redirectToLogin, user]);

    const isActive =
        simulation !== null &&
        !["completed", "failed"].includes(simulation.status);

    useEffect(() => {
        const preference = window.sessionStorage.getItem("sakinah.showScoringPanel");
        if (preference !== null) setShowScoringPanel(preference === "true");
    }, []);

    useEffect(() => {
        window.sessionStorage.setItem("sakinah.showScoringPanel", String(showScoringPanel));
    }, [showScoringPanel]);

    useEffect(() => {
        const container = transcriptRef.current;
        if (container) container.scrollTop = container.scrollHeight;
    }, [turns]);

    const teardownAudio = useCallback(() => {
        audioWsRef.current?.close();
        audioWsRef.current = null;
        try {
            audioKeepAliveRef.current?.stop();
        } catch {
            // The source may already have stopped during context teardown.
        }
        audioKeepAliveRef.current = null;
        void audioCtxRef.current?.close().catch(() => undefined);
        audioCtxRef.current = null;
        gainRef.current = null;
        nextPlayTimeRef.current = 0;
        setAudioStatus("idle");
        setAudioFrameCount(0);
    }, []);

    useEffect(
        () => () => {
            wsRef.current?.close();
            teardownAudio();
        },
        [teardownAudio],
    );

    // Release the audio pipeline once the simulation has finished.
    useEffect(() => {
        if (simulation && !isActive) teardownAudio();
    }, [simulation, isActive, teardownAudio]);

    const handleEvent = useCallback((event: SimulationEvent) => {
        if (event.type === "simulation-status") {
            setSimulation(event.payload as unknown as SimulationResponse);
            const snapshot = event.payload as unknown as SimulationResponse & { calm_scores?: Record<string, unknown>; calm_trend?: CalmAnalysis["trend"] };
            if (snapshot.calm_scores && Object.keys(snapshot.calm_scores).length > 0) {
                setCalmAnalysis({ calm_scores: snapshot.calm_scores as CalmAnalysis["calm_scores"], trend: snapshot.calm_trend });
            }
            return;
        }
        if (event.type === "calm-analysis") {
            setCalmAnalysis(event.payload as unknown as CalmAnalysis);
            return;
        }
        if (event.type === "pipeline-error" || event.type === "rtf-pipeline-error") {
            const message = (event.payload?.error as string) ?? "Pipeline error";
            setError(`${ROLE_LABELS[event.role] ?? event.role}: ${message}`);
            return;
        }
        if (event.role !== "sakinah" && event.role !== "service_user") return;
        const role = event.role as SimTurn["role"];
        if (event.type === "calm-evaluation") {
            const turnId = event.payload?.turn_id as string | undefined;
            const status = event.payload?.status as TurnEvaluation["status"] | undefined;
            if (!turnId || !status) return;
            setTurns((previous) => previous.map((turn) => turn.id === turnId
                ? {
                    ...turn,
                    evaluation: {
                        status,
                        result: event.payload?.result as CalmEvaluationResult | undefined,
                        error: event.payload?.error as string | undefined,
                    },
                }
                : turn));
            return;
        }
        // The observer streams word/phrase-level rtf-bot-text chunks;
        // consecutive chunks from the same role form one spoken turn, closed
        // by that role's rtf-bot-stopped-speaking.
        if (event.type === "rtf-bot-text") {
            const text = ((event.payload?.text as string) ?? "").trim();
            if (!text) return;
            const turnId = (event.payload?.turn_id as string | undefined) ??
                `${role}-${event.timestamp ?? Date.now()}`;
            setTurns((previous) => {
                const last = previous[previous.length - 1];
                if (last && last.role === role && !last.final) {
                    return [
                        ...previous.slice(0, -1),
                        { ...last, text: `${last.text} ${text}` },
                    ];
                }
                return [
                    ...previous.map((turn) => ({ ...turn, final: true })),
                    {
                        id: turnId,
                        role,
                        text,
                        final: false,
                        timestamp:
                            event.timestamp ??
                            (event.payload?.timestamp as string | undefined),
                    },
                ];
            });
            return;
        }
        if (event.type === "rtf-bot-stopped-speaking") {
            const turnId = event.payload?.turn_id as string | undefined;
            setTurns((previous) =>
                previous.map((turn, index) =>
                    (turnId ? turn.id === turnId : index === previous.length - 1 && turn.role === role)
                        ? { ...turn, final: true }
                        : turn,
                ),
            );
        }
    }, []);

    const openEventsSocket = useCallback(
        (simulationId: string, token: string) => {
            const baseUrl =
                client.getConfig().baseUrl || resolveBrowserBackendUrl();
            const wsUrl = baseUrl.replace(/^http/, "ws");
            const socket = new WebSocket(
                `${wsUrl}/api/v1/sakinah/simulations/${simulationId}/events?token=${token}`,
            );
            socket.onmessage = (message) => {
                try {
                    handleEvent(JSON.parse(message.data) as SimulationEvent);
                } catch {
                    // Ignore malformed events.
                }
            };
            socket.onerror = () => {
                setError("Lost connection to the simulation event stream.");
            };
            wsRef.current = socket;
        },
        [handleEvent],
    );

    const SIM_AUDIO_SAMPLE_RATE = 16000;

    const prepareAudioContext = useCallback(() => {
        if (audioCtxRef.current) return audioCtxRef.current;
        const AudioContextConstructor =
            window.AudioContext ??
            (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
        if (!AudioContextConstructor) {
            throw new Error("This browser does not support Simulation audio playback.");
        }
        // Create the context synchronously from the Start button handler. Safari
        // may otherwise keep a context created after an awaited API request
        // suspended under its autoplay policy.
        const ctx = new AudioContextConstructor();
        const gain = ctx.createGain();
        gain.connect(ctx.destination);
        // Keep a silent source alive while the simulation is running. Safari
        // can suspend an otherwise idle context between the Start gesture and
        // the first network-delivered audio chunk; a short unlock beep alone
        // does not reliably prevent that suspension.
        const unlockGain = ctx.createGain();
        unlockGain.gain.value = 0;
        unlockGain.connect(ctx.destination);
        const keepAlive = ctx.createOscillator();
        keepAlive.frequency.value = 20;
        keepAlive.connect(unlockGain);
        keepAlive.start();
        audioKeepAliveRef.current = keepAlive;
        audioCtxRef.current = ctx;
        gainRef.current = gain;
        nextPlayTimeRef.current = 0;
        return ctx;
    }, []);

    const openAudioSocket = useCallback(
        (simulationId: string, token: string, ctx: AudioContext) => {

            const baseUrl =
                client.getConfig().baseUrl || resolveBrowserBackendUrl();
            const wsUrl = baseUrl.replace(/^http/, "ws");
            const socket = new WebSocket(
                `${wsUrl}/api/v1/sakinah/simulations/${simulationId}/audio?token=${token}`,
            );
            socket.binaryType = "arraybuffer";
            setAudioStatus("connecting");
            socket.onopen = () => {
                // The context was unlocked from Start; this is a defensive
                // resume for browsers that suspend it while the socket opens.
                if (ctx.state !== "running") void ctx.resume().catch(() => undefined);
                setAudioStatus("connected");
            };
            socket.onerror = () => {
                setAudioStatus("error");
            };
            socket.onmessage = async (message) => {
                const context = audioCtxRef.current === ctx ? ctx : null;
                const gainNode = gainRef.current;
                if (!context || !gainNode) return;
                try {
                    if (context.state !== "running") {
                        await context.resume();
                    }
                    if (context.state !== "running") {
                        throw new Error(`AudioContext is ${context.state}`);
                    }
                    const bytes = message.data instanceof Blob
                        ? await message.data.arrayBuffer()
                        : message.data as ArrayBuffer;
                    const pcm = new Int16Array(bytes);
                    if (pcm.length === 0) return;
                    setAudioFrameCount((count) => count + 1);
                    const samples = new Float32Array(pcm.length);
                    for (let i = 0; i < pcm.length; i += 1) {
                        samples[i] = pcm[i] / 32768;
                    }
                    const buffer = context.createBuffer(
                        1,
                        samples.length,
                        SIM_AUDIO_SAMPLE_RATE,
                    );
                    buffer.copyToChannel(samples, 0);
                    const source = context.createBufferSource();
                    source.buffer = buffer;
                    source.connect(gainNode);
                    // Schedule chunks back-to-back with a small jitter cushion.
                    const startAt = Math.max(
                        context.currentTime + 0.1,
                        nextPlayTimeRef.current,
                    );
                    source.start(startAt);
                    nextPlayTimeRef.current = startAt + buffer.duration;
                } catch {
                    setAudioStatus("error");
                }
            };
            audioWsRef.current = socket;
        },
        [],
    );

    const toggleMute = () => {
        setMuted((previous) => {
            const next = !previous;
            if (gainRef.current) gainRef.current.gain.value = next ? 0 : 1;
            return next;
        });
    };

    const startSimulation = async () => {
        setStarting(true);
        setError(null);
        setTurns([]);
        setCalmAnalysis(null);
        try {
            if (!isAuthenticated) {
                redirectToLogin();
                return;
            }
            // Resume the context before the first await so browser autoplay
            // policies, especially Safari's, treat this as the Start gesture.
            const audioContext = prepareAudioContext();
            if (audioContext.state !== "running") await audioContext.resume();
            if (audioContext.state !== "running") {
                throw new Error(`Browser audio is ${audioContext.state}; allow audio and try again.`);
            }
            setAudioFrameCount(0);
            // The SDK's auth interceptor signs the request; the token is only
            // needed for the events WebSocket, which cannot use the interceptor.
            const token = await getAccessToken();
            const response = await startSimulationApiV1SakinahSimulationsPost({
                body: {
                    scenario: scenario.trim(),
                    scenario_id: scenarioId,
                    scenario_name: scenarioName,
                    experiment_mode: experimentMode,
                },
            });
            if (response.response?.status === 401) {
                redirectToLogin();
                return;
            }
            if (response.response?.status === 403) {
                throw new Error('You are not authorized to start this simulation.');
            }
            if (response.error || !response.data) {
                throw new Error(
                    detailFromError(response.error, "Unable to start the simulation."),
                );
            }
            const snapshot = response.data;
            setSimulation(snapshot);
            openEventsSocket(snapshot.simulation_id, token);
            openAudioSocket(snapshot.simulation_id, token, audioContext);
        } catch (startError) {
            teardownAudio();
            setError(
                startError instanceof Error
                    ? startError.message
                    : "Unable to start the simulation.",
            );
        } finally {
            setStarting(false);
        }
    };

    const stopSimulation = async () => {
        if (!simulation) return;
        setStopping(true);
        try {
            const response = await stopSimulationApiV1SakinahSimulationsSimulationIdStopPost({
                path: { simulation_id: simulation.simulation_id },
            });
            if (response.response?.status === 401) {
                redirectToLogin();
                return;
            }
            if (response.response?.status === 403) {
                throw new Error('You are not authorized to stop this simulation.');
            }
            if (response.error || !response.data) {
                throw new Error(
                    detailFromError(response.error, "Unable to stop the simulation."),
                );
            }
            setSimulation(response.data);
            if (["completed", "failed"].includes(response.data.status)) {
                setHistoryRefreshKey((previous) => previous + 1);
            }
        } catch (stopError) {
            setError(
                stopError instanceof Error
                    ? stopError.message
                    : "Unable to stop the simulation.",
            );
        } finally {
            setStopping(false);
        }
    };

    return (
        <main className="mx-auto w-full max-w-6xl space-y-6 p-4 md:p-8">
            <header className="space-y-2">
                <p className="text-sm font-medium uppercase tracking-[0.2em] text-primary">
                    Sakinah
                </p>
                <h1 className="text-3xl font-bold tracking-tight">
                    AI-to-AI Simulation
                </h1>
                <p className="max-w-2xl text-muted-foreground">
                    A simulated service user speaks with Sakinah automatically.
                    Watch and listen to the conversation live — no microphone
                    needed.
                </p>
                <Link
                    href="/sakinah"
                    className="text-sm text-primary underline underline-offset-4"
                >
                    Switch to the live voice console
                </Link>
            </header>
            {error ? (
                <p className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
                    {error}
                </p>
            ) : null}
            <div className="grid gap-6 lg:grid-cols-3">
                <section className="space-y-3 rounded-xl border bg-card p-5 shadow-sm">
                    <div className="space-y-1">
                        <h2 className="text-lg font-semibold">Scenario</h2>
                        <p className="text-sm text-muted-foreground">
                            Describe the service user the simulator should
                            roleplay for this session.
                        </p>
                    </div>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <Label htmlFor="sim-scenario">Scenario instructions</Label>
                        <Link
                            href="/sakinah/scenarios"
                            className="text-sm text-primary underline underline-offset-4"
                        >
                            Choose from Scenario Library
                        </Link>
                    </div>
                    <Textarea
                        id="sim-scenario"
                        value={scenario}
                        onChange={(event) => setScenario(event.target.value)}
                        disabled={isActive}
                        rows={9}
                        placeholder="A service user presents with..."
                        className="resize-y"
                    />
                    {!isActive ? (
                        <div className="space-y-1">
                            <Label htmlFor="sim-experiment-mode">Experiment mode</Label>
                            <select
                                id="sim-experiment-mode"
                                value={experimentMode}
                                onChange={(event) => setExperimentMode(event.target.value as ExperimentMode)}
                                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
                            >
                                <option value="baseline">Baseline</option>
                                <option value="scores_only">Scores only</option>
                                <option value="scores_and_trends">Scores + trends</option>
                                <option value="full_calm_prompt">Full CALM prompt</option>
                            </select>
                        </div>
                    ) : null}
                    {isActive ? (
                        <div className="flex flex-wrap gap-2">
                            <Button
                                type="button"
                                variant="destructive"
                                onClick={() => void stopSimulation()}
                                disabled={stopping}
                                className="w-full sm:w-auto"
                            >
                                {stopping ? <Loader2 className="animate-spin" /> : <Square />}
                                {stopping ? "Stopping..." : "Stop Simulation"}
                            </Button>
                            <Button
                                type="button"
                                variant="outline"
                                onClick={toggleMute}
                                aria-label={muted ? "Unmute audio" : "Mute audio"}
                            >
                                {muted ? <VolumeX /> : <Volume2 />}
                                {muted ? "Unmute" : "Mute"}
                            </Button>
                            <Button
                                type="button"
                                variant="outline"
                                onClick={() => setShowScoringPanel((previous) => !previous)}
                                aria-label={showScoringPanel ? "Hide scoring" : "Show scoring"}
                                title={showScoringPanel ? "Hide scoring" : "Show scoring"}
                            >
                                {showScoringPanel ? <EyeOff /> : <ChartBar />}
                                {showScoringPanel ? "Hide scoring" : "Show scoring"}
                            </Button>
                        </div>
                    ) : (
                        <Button
                            type="button"
                            onClick={() => void startSimulation()}
                            disabled={starting || !scenario.trim()}
                            className="w-full sm:w-auto"
                        >
                            {starting ? <Loader2 className="animate-spin" /> : <Play />}
                            {starting ? "Starting..." : "Start Simulation"}
                        </Button>
                    )}
                    {simulation ? (
                        <div className="space-y-1 border-t pt-3 text-xs text-muted-foreground">
                            <p>
                                Status:{" "}
                                <span className="font-medium text-foreground">
                                    {simulation.status}
                                </span>
                                {simulation.stop_reason
                                    ? ` (${simulation.stop_reason})`
                                    : null}
                            </p>
                            {Object.entries(simulation.agents).map(([role, info]) => (
                                <p key={role}>
                                    {ROLE_LABELS[role] ?? role}: workflow{" "}
                                    {info.workflow_id}, run {info.workflow_run_id}
                                </p>
                            ))}
                            {isActive ? (
                                <p>
                                    Audio: {audioStatus}
                                    {audioFrameCount > 0 ? ` (${audioFrameCount} chunks received)` : ""}
                                </p>
                            ) : null}
                        </div>
                    ) : null}
                </section>
                <section className="flex min-h-[28rem] flex-col rounded-xl border bg-card shadow-sm lg:col-span-2">
                    <div className="border-b px-5 py-4">
                        <h2 className="text-lg font-semibold">Live transcript</h2>
                        <p className="text-sm text-muted-foreground">
                            Service User and Sakinah turns appear as they are
                            spoken.
                        </p>
                    </div>
                    <div
                        ref={transcriptRef}
                        aria-live="polite"
                        className="flex-1 space-y-4 overflow-y-auto p-5"
                    >
                        {turns.length === 0 ? (
                            <p className="pt-16 text-center text-sm text-muted-foreground">
                                The conversation will appear here.
                            </p>
                        ) : (
                            turns.map((turn) => (
                                <article
                                    key={turn.id}
                                    className={cn(
                                        "rounded-lg border p-3",
                                        turn.role === "service_user"
                                            ? "mr-8 bg-muted/40"
                                            : "ml-8 bg-primary/5",
                                    )}
                                >
                                    <div className="mb-1 flex items-center justify-between gap-3">
                                        <span className="text-xs font-semibold tracking-wide">
                                            {ROLE_LABELS[turn.role]}
                                        </span>
                                        {!turn.final ? (
                                            <span className="text-xs text-muted-foreground">
                                                speaking…
                                            </span>
                                        ) : null}
                                    </div>
                                    <p className="whitespace-pre-wrap text-sm leading-relaxed">
                                        {turn.text}
                                    </p>
                                    {showScoringPanel && turn.final ? (
                                        <CalmEvaluationPanel evaluation={turn.evaluation} />
                                    ) : null}
                                </article>
                            ))
                        )}
                    </div>
                </section>
            </div>
            {showScoringPanel ? <div className="lg:ml-[calc(33.333%+0.5rem)]"><CalmScoringPanel analysis={calmAnalysis} /></div> : null}
            <SakinahRunHistory refreshKey={historyRefreshKey} />
        </main>
    );
}
