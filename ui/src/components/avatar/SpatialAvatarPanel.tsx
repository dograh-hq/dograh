'use client';

import { Loader2, Play } from 'lucide-react';
import { RefObject, useCallback, useEffect, useRef, useState } from 'react';

import { useAppConfig } from '@/context/AppConfigContext';
import { resolveBrowserBackendUrl } from '@/lib/apiClient';
import logger from '@/lib/logger';

type AvatarKitModule = typeof import('@spatialwalk/avatarkit');
type AvatarViewInstance = InstanceType<AvatarKitModule['AvatarView']>;

interface AvatarSessionInfo {
    app_id: string;
    avatar_id: string;
    session_token: string;
    expires_at: number;
}

interface SpatialAvatarPanelProps {
    /** Bearer token used to mint the SpatialReal session token via the backend. */
    accessToken: string | null;
    /** Whether the voice call is currently active. */
    active: boolean;
    /** True while the bot is speaking (from rtf-bot-started/stopped-speaking). */
    botSpeaking: boolean;
    /** Audio element that receives the remote WebRTC bot track. */
    audioRef: RefObject<HTMLAudioElement | null>;
    /** Workflow run id — used by host mode to join the avatar relay WS. */
    workflowRunId?: number;
    /**
     * Reports whether the avatar will drive this run. When true the call must
     * NOT auto-start — the avatar becomes the sole audio sink and the call is
     * started only once the user completes the start gesture. When false the
     * caller behaves as a normal audio-only call.
     */
    onAvatarWillDrive?: (willDrive: boolean) => void;
    /** Fired when the user gesture is complete and the call should start. */
    onRequestCallStart?: () => void;
    /**
     * When set, the panel authenticates via the public embed endpoints
     * (/public/embed/avatar/{config,session}/{token}) instead of the
     * authenticated ones — used by the iframe embed page.
     */
    publicSessionToken?: string;
    /**
     * Bare mode: render only the avatar canvas (no border, background, loading,
     * gesture or error overlays) so a parent can supply its own chrome. The
     * parent drives start via `startSignal` and reads status via `onStateChange`.
     */
    bare?: boolean;
    /** In bare mode, increment this to trigger the start gesture externally. */
    startSignal?: number;
    /** Reports panel state + load progress so a parent can render its own UI. */
    onStateChange?: (state: PanelState, loadProgress: number) => void;
    /**
     * Tailwind height class for the avatar canvas in full (non-bare) mode.
     * Defaults to `h-64` (the in-app tester size); the standalone embed page
     * passes a larger value to fill the screen.
     */
    heightClass?: string;
    /**
     * Full-bleed mode: the panel fills its parent with no rounded corners or
     * border (background image edge-to-edge). Used by the full-screen embed.
     */
    fullBleed?: boolean;
}

type PanelState =
    | 'checking' // fetching /avatar/config
    | 'disabled' // avatar not configured on this deployment
    | 'loading' // SDK init + avatar download
    | 'gesture' // loaded, waiting for a user click to unlock audio
    | 'ready' // connected and driving
    | 'error';

const TARGET_SAMPLE_RATE = 16000;

/** Downsample Float32 PCM to 16kHz mono Int16 (linear interpolation). */
function toPCM16(input: Float32Array, inputRate: number): ArrayBuffer {
    const ratio = inputRate / TARGET_SAMPLE_RATE;
    const outLength = Math.floor(input.length / ratio);
    const out = new Int16Array(outLength);
    for (let i = 0; i < outLength; i++) {
        const pos = i * ratio;
        const idx = Math.floor(pos);
        const frac = pos - idx;
        const a = input[idx] ?? 0;
        const b = input[idx + 1] ?? a;
        const sample = a + (b - a) * frac;
        const clamped = Math.max(-1, Math.min(1, sample));
        out[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
    }
    return out.buffer;
}

export function SpatialAvatarPanel({
    accessToken,
    active,
    botSpeaking,
    audioRef,
    workflowRunId,
    onAvatarWillDrive,
    onRequestCallStart,
    publicSessionToken,
    bare,
    startSignal,
    onStateChange,
    heightClass = 'h-64',
    fullBleed,
}: SpatialAvatarPanelProps) {
    const { config: appConfig } = useAppConfig();
    const containerRef = useRef<HTMLDivElement>(null);
    const avatarViewRef = useRef<AvatarViewInstance | null>(null);
    const audioCtxRef = useRef<AudioContext | null>(null);
    const processorRef = useRef<ScriptProcessorNode | null>(null);
    const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
    const botSpeakingRef = useRef(false);
    const wasSendingRef = useRef(false);
    const startedRef = useRef(false);
    const relayWsRef = useRef<WebSocket | null>(null);
    const conversationIdRef = useRef<string | null>(null);
    const kitRef = useRef<AvatarKitModule | null>(null);
    const sessionExpiresAtRef = useRef<number>(0);
    const callStartedRef = useRef(false);
    // Keep callbacks in refs so the boot effect doesn't re-run when the parent
    // re-renders new callback identities.
    const onAvatarWillDriveRef = useRef(onAvatarWillDrive);
    const onRequestCallStartRef = useRef(onRequestCallStart);
    onAvatarWillDriveRef.current = onAvatarWillDrive;
    onRequestCallStartRef.current = onRequestCallStart;
    const [state, setState] = useState<PanelState>('checking');
    const [mode, setMode] = useState<'sdk' | 'host'>('sdk');
    const [loadProgress, setLoadProgress] = useState(0);
    const [errorMessage, setErrorMessage] = useState<string | null>(null);

    botSpeakingRef.current = botSpeaking;

    // Report state/progress to a parent (bare mode renders its own chrome).
    const onStateChangeRef = useRef(onStateChange);
    onStateChangeRef.current = onStateChange;
    useEffect(() => {
        onStateChangeRef.current?.(state, loadProgress);
    }, [state, loadProgress]);

    // The public embed is always served from the same origin it must reach, so
    // use window.location.origin for it. The deployment-reported
    // backendApiEndpoint (e.g. an internal http://localhost:8000) is not
    // browser-reachable and would 404 the avatar config. Authenticated in-app
    // usage keeps honoring backendApiEndpoint.
    const backendUrl = publicSessionToken
        ? resolveBrowserBackendUrl()
        : resolveBrowserBackendUrl(appConfig?.backendApiEndpoint);

    // Start the underlying call exactly once. The avatar is the sole audio
    // sink, so the call must not begin until the avatar is enabled (or has
    // failed and fallen back to audio-only).
    const startCallOnce = useCallback(() => {
        if (callStartedRef.current) return;
        callStartedRef.current = true;
        onRequestCallStartRef.current?.();
    }, []);

    // ── Phase A: config check + SDK init + avatar load (no gesture needed) ──
    useEffect(() => {
        if (!accessToken && !publicSessionToken) return;
        let cancelled = false;
        const containerEl = containerRef.current;

        async function boot() {
            try {
                const headers: Record<string, string> = { 'Content-Type': 'application/json' };
                if (!publicSessionToken && accessToken) {
                    headers.Authorization = `Bearer ${accessToken}`;
                }

                // Public (iframe embed) mode uses the token-scoped public
                // endpoints; authenticated mode uses the run-scoped ones.
                const configUrl = publicSessionToken
                    ? `${backendUrl}/api/v1/public/embed/avatar/config/${publicSessionToken}`
                    : `${backendUrl}/api/v1/avatar/config${workflowRunId ? `?workflow_run_id=${workflowRunId}` : ''}`;
                const sessionUrl = publicSessionToken
                    ? `${backendUrl}/api/v1/public/embed/avatar/session/${publicSessionToken}`
                    : `${backendUrl}/api/v1/avatar/session${workflowRunId ? `?workflow_run_id=${workflowRunId}` : ''}`;

                const configRes = await fetch(configUrl, { headers });
                if (!configRes.ok) throw new Error(`avatar config: HTTP ${configRes.status}`);
                const config = await configRes.json();
                if (cancelled) return;
                if (!config.enabled) {
                    setState('disabled');
                    // No avatar for this run — let the caller start the call
                    // normally (audio-only).
                    onAvatarWillDriveRef.current?.(false);
                    return;
                }
                const drivingMode: 'sdk' | 'host' = config.mode === 'host' ? 'host' : 'sdk';
                setMode(drivingMode);

                // The avatar will drive this run: it becomes the only audio
                // sink (mute the raw element now, before any bot audio arrives)
                // and the caller must NOT auto-start the call.
                onAvatarWillDriveRef.current?.(true);
                if (audioRef.current) audioRef.current.muted = true;

                setState('loading');
                const sessionRes = await fetch(sessionUrl, { method: 'POST', headers });
                if (!sessionRes.ok) throw new Error(`avatar session: HTTP ${sessionRes.status}`);
                const session: AvatarSessionInfo = await sessionRes.json();
                if (cancelled) return;

                const kit = await import('@spatialwalk/avatarkit');
                if (cancelled) return;
                kitRef.current = kit;
                sessionExpiresAtRef.current = session.expires_at ?? 0;

                const wantedDrivingMode =
                    drivingMode === 'host'
                        ? kit.DrivingServiceMode.host
                        : kit.DrivingServiceMode.sdk;
                // The SDK is a singleton: if a previous boot initialized it in a
                // different driving mode (e.g. sdk before this run's host config
                // resolved), it must be torn down or the mode silently sticks.
                if (
                    kit.AvatarSDK.isInitialized &&
                    kit.AvatarSDK.configuration?.drivingServiceMode !== wantedDrivingMode
                ) {
                    logger.info(
                        `Avatar SDK driving mode change (${kit.AvatarSDK.configuration?.drivingServiceMode} -> ${wantedDrivingMode}); reinitializing`
                    );
                    kit.AvatarSDK.cleanup();
                }
                if (!kit.AvatarSDK.isInitialized) {
                    await kit.AvatarSDK.initialize(session.app_id, {
                        environment: kit.Environment.intl,
                        drivingServiceMode: wantedDrivingMode,
                        audioFormat: { channelCount: 1, sampleRate: TARGET_SAMPLE_RATE },
                    });
                }
                kit.AvatarSDK.setSessionToken(session.session_token);
                if (cancelled) return;

                const avatar = await kit.AvatarManager.shared.load(
                    session.avatar_id,
                    (progress) => {
                        if (!cancelled) setLoadProgress(Math.round(progress.progress ?? 0));
                    }
                );
                if (cancelled || !containerRef.current) return;

                const view = new kit.AvatarView(avatar, containerRef.current);
                view.controller.onError = (err) => {
                    logger.error(`Avatar controller error: ${JSON.stringify(err)}`);
                };
                avatarViewRef.current = view;
                setState('gesture');
            } catch (err) {
                if (cancelled) return;
                logger.error(`Avatar setup failed: ${err}`);
                setErrorMessage(err instanceof Error ? err.message : 'Avatar setup failed');
                setState('error');
                // Avatar failed to set up — restore the raw audio and let the
                // call proceed audio-only (it may have been gated on us).
                if (audioRef.current) audioRef.current.muted = false;
                startCallOnce();
            }
        }

        void boot();

        return () => {
            cancelled = true;
            void containerEl;
            avatarViewRef.current?.dispose();
            avatarViewRef.current = null;
            startedRef.current = false;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [accessToken, backendUrl, workflowRunId, publicSessionToken]);

    // ── Phase B: user gesture unlock → connect to driving service ──
    // SDK mode also opens the SDK's own WebSocket via start(); in host mode
    // the backend owns that connection, so only the audio context is needed.
    const enableAvatar = useCallback(async () => {
        const view = avatarViewRef.current;
        if (!view || startedRef.current) return;
        try {
            await view.controller.initializeAudioContext();
            if (mode === 'sdk') {
                await view.controller.start();
            }
            startedRef.current = true;
            setState('ready');
            // Avatar is live and listening — NOW start the call so the very
            // first bot word is captured and lip-synced (and only the avatar
            // is audible; the raw element stays muted).
            if (audioRef.current) audioRef.current.muted = true;
            startCallOnce();
        } catch (err) {
            logger.error(`Avatar start failed: ${err}`);
            setErrorMessage(err instanceof Error ? err.message : 'Avatar start failed');
            setState('error');
            if (audioRef.current) audioRef.current.muted = false;
            startCallOnce();
        }
    }, [mode, audioRef, startCallOnce]);

    // Bare mode: a parent triggers the start gesture by bumping startSignal.
    useEffect(() => {
        if (!startSignal) return;
        if (state === 'gesture') void enableAvatar();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [startSignal]);

    // ── Session token refresh: re-mint ~10 min before expiry ──
    useEffect(() => {
        // Public embed sessions are short-lived and not refreshed client-side.
        if (state !== 'ready' || !accessToken || publicSessionToken) return;
        const expiresAt = sessionExpiresAtRef.current;
        if (!expiresAt) return;
        const refreshInMs = Math.max(
            60_000,
            expiresAt * 1000 - Date.now() - 10 * 60_000
        );
        const timer = setTimeout(async () => {
            try {
                const res = await fetch(`${backendUrl}/api/v1/avatar/session`, {
                    method: 'POST',
                    headers: {
                        Authorization: `Bearer ${accessToken}`,
                        'Content-Type': 'application/json',
                    },
                });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const session: AvatarSessionInfo = await res.json();
                sessionExpiresAtRef.current = session.expires_at ?? 0;
                kitRef.current?.AvatarSDK.setSessionToken(session.session_token);
            } catch (err) {
                logger.error(`Avatar token refresh failed: ${err}`);
            }
        }, refreshInMs);
        return () => clearTimeout(timer);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [state, accessToken]);

    // ── Phase C (host mode): consume the backend avatar relay WS ──
    useEffect(() => {
        if (mode !== 'host' || state !== 'ready' || !active) return;
        if (!workflowRunId || !accessToken) return;

        const wsBase = backendUrl.replace(/^http/, 'ws');
        let disposed = false;
        let attempts = 0;
        let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

        const connect = () => {
            if (disposed) return;
            const url = `${wsBase}/api/v1/ws/avatar/${workflowRunId}?token=${encodeURIComponent(accessToken)}`;
            logger.info(`Avatar relay WS connecting: ${wsBase}/api/v1/ws/avatar/${workflowRunId}`);
            const ws = new WebSocket(url);
            ws.binaryType = 'arraybuffer';
            relayWsRef.current = ws;
            wireSocket(ws);
        };

        const wireSocket = (ws: WebSocket) => {
            ws.onmessage = (event) => {
            const view = avatarViewRef.current;
            if (!view) return;
            if (typeof event.data === 'string') {
                try {
                    const control = JSON.parse(event.data);
                    if (control.type === 'avatar-error') {
                        logger.error(`Avatar relay error: ${control.detail}`);
                        if (audioRef.current) audioRef.current.muted = false;
                        setState('error');
                    } else if (control.type === 'avatar-interrupted') {
                        view.controller.interrupt();
                    }
                } catch {
                    // Ignore malformed control messages.
                }
                return;
            }
            const data = new Uint8Array(event.data as ArrayBuffer);
            if (data.length < 2) return;
            const msgType = data[0];
            const isLast = (data[1] & 0x01) === 0x01;
            const payload = data.subarray(2);
            if (msgType === 0x01) {
                const conversationId = view.controller.yieldAudioData(payload, isLast);
                if (conversationId) conversationIdRef.current = conversationId;
            } else if (msgType === 0x02) {
                const conversationId =
                    conversationIdRef.current ?? view.controller.getCurrentConversationId();
                if (conversationId) {
                    view.controller.yieldFramesData([payload], conversationId);
                }
            }
        };
            ws.onopen = () => {
                attempts = 0;
                logger.info('Avatar relay WS connected');
                // Avatar SDK is now the audio sink; mute the raw WebRTC element
                // but keep it flowing as the instant fallback.
                if (audioRef.current) audioRef.current.muted = true;
            };
            ws.onerror = () => {
                logger.error('Avatar relay WS error');
            };
            ws.onclose = (event) => {
                logger.info(`Avatar relay WS closed (code ${event.code})`);
                if (audioRef.current) audioRef.current.muted = false;
                // Reconnect with backoff (1s / 3s / 7s) while the call is live.
                if (!disposed && attempts < 3) {
                    const delay = [1000, 3000, 7000][attempts];
                    attempts += 1;
                    reconnectTimer = setTimeout(connect, delay);
                }
            };
        };

        connect();

        return () => {
            disposed = true;
            if (reconnectTimer) clearTimeout(reconnectTimer);
            const ws = relayWsRef.current;
            relayWsRef.current = null;
            try {
                ws?.close();
            } catch {
                // Already closed.
            }
            if (audioRef.current) audioRef.current.muted = false;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [mode, state, active, workflowRunId, accessToken]);

    // ── Phase C (SDK mode): bridge remote WebRTC bot audio into the avatar ──
    useEffect(() => {
        if (mode !== 'sdk' || state !== 'ready' || !active) return;

        let cancelled = false;
        let pollTimer: ReturnType<typeof setTimeout> | null = null;

        function attach(stream: MediaStream) {
            if (cancelled) return;
            try {
                const ctx = new AudioContext();
                const source = ctx.createMediaStreamSource(stream);
                // ScriptProcessor is deprecated but universally supported and
                // avoids shipping a separate AudioWorklet module file.
                const processor = ctx.createScriptProcessor(2048, 1, 1);
                processor.onaudioprocess = (event) => {
                    const view = avatarViewRef.current;
                    if (!view) return;
                    if (botSpeakingRef.current) {
                        const chunk = toPCM16(
                            event.inputBuffer.getChannelData(0),
                            ctx.sampleRate
                        );
                        view.controller.send(chunk, false);
                        wasSendingRef.current = true;
                    } else if (wasSendingRef.current) {
                        // Bot turn ended — close the round so the avatar
                        // finishes remaining animation and returns to idle.
                        view.controller.send(new ArrayBuffer(0), true);
                        wasSendingRef.current = false;
                    }
                };
                source.connect(processor);
                // Keep the graph alive without echoing audio: gain 0 to output.
                const silent = ctx.createGain();
                silent.gain.value = 0;
                processor.connect(silent);
                silent.connect(ctx.destination);

                audioCtxRef.current = ctx;
                sourceRef.current = source;
                processorRef.current = processor;

                // The avatar SDK is now the audio sink; mute the raw element.
                if (audioRef.current) audioRef.current.muted = true;
            } catch (err) {
                logger.error(`Avatar audio bridge failed: ${err}`);
            }
        }

        function waitForStream() {
            if (cancelled) return;
            const stream = audioRef.current?.srcObject;
            if (stream instanceof MediaStream && stream.getAudioTracks().length > 0) {
                attach(stream);
            } else {
                pollTimer = setTimeout(waitForStream, 250);
            }
        }

        waitForStream();

        return () => {
            cancelled = true;
            if (pollTimer) clearTimeout(pollTimer);
            if (wasSendingRef.current) {
                avatarViewRef.current?.controller.send(new ArrayBuffer(0), true);
                wasSendingRef.current = false;
            }
            processorRef.current?.disconnect();
            sourceRef.current?.disconnect();
            void audioCtxRef.current?.close();
            processorRef.current = null;
            sourceRef.current = null;
            audioCtxRef.current = null;
            if (audioRef.current) audioRef.current.muted = false;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [mode, state, active]);

    if (state === 'disabled') return null;

    // Bare mode: just the avatar canvas, filling its (parent-styled) container.
    // The parent renders loading/start/error chrome from onStateChange.
    if (bare) {
        return <div ref={containerRef} className="h-full w-full" />;
    }

    return (
        <div
            className={
                fullBleed
                    ? 'relative h-full w-full overflow-hidden bg-cover bg-center'
                    : 'relative w-full overflow-hidden rounded-xl border border-border/70 bg-muted/20 bg-cover bg-center'
            }
            style={{ backgroundImage: "url('/avatar-background.jpg')" }}
        >
            <div ref={containerRef} className={`w-full ${fullBleed ? 'h-full' : heightClass}`} />
            {state === 'checking' || state === 'loading' ? (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-background/70 text-sm text-muted-foreground">
                    <Loader2 className="h-5 w-5 animate-spin" />
                    {state === 'loading' ? `Loading avatar… ${loadProgress}%` : 'Checking avatar…'}
                </div>
            ) : null}
            {state === 'gesture' ? (
                <button
                    type="button"
                    onClick={() => void enableAvatar()}
                    className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-background/60 text-sm font-medium text-foreground transition-colors hover:bg-background/40"
                >
                    <span className="flex h-10 w-10 items-center justify-center rounded-full bg-primary text-primary-foreground">
                        <Play className="h-5 w-5" />
                    </span>
                    Start call
                </button>
            ) : null}
            {state === 'error' ? (
                <div className="absolute inset-0 flex items-center justify-center bg-background/70 px-4 text-center text-xs text-muted-foreground">
                    Avatar unavailable — call continues with audio only.
                    {errorMessage ? <span className="sr-only">{errorMessage}</span> : null}
                </div>
            ) : null}
        </div>
    );
}
