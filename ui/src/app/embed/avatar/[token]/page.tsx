'use client';

import { Loader2, MessageSquare, Mic, MicOff, PhoneOff, X } from 'lucide-react';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';

import { SpatialAvatarPanel } from '@/components/avatar/SpatialAvatarPanel';
import { resolveBrowserBackendUrl } from '@/lib/apiClient';
import logger from '@/lib/logger';

interface InitResponse {
    session_token: string;
    workflow_run_id: number;
    config?: { turn_enabled?: boolean; force_turn_relay?: boolean };
}

interface TranscriptMsg {
    id: string;
    role: 'bot' | 'user';
    text: string;
    final: boolean;
}

/**
 * Standalone, frameable avatar agent — the page loaded by the copy-paste
 * <iframe>. Full-screen: the avatar background fills the viewport, with a live
 * transcript panel and call controls (mute / end call) overlaid. Renders the
 * same SpatialAvatarPanel used in the in-app tester; the panel owns the start
 * gesture, this page owns the public WebRTC call + transcript + controls.
 */
export default function AvatarEmbedPage() {
    const params = useParams();
    const token = String(params.token ?? '');
    const backendUrl = resolveBrowserBackendUrl();

    const [init, setInit] = useState<InitResponse | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [callActive, setCallActive] = useState(false);
    const [callEnded, setCallEnded] = useState(false);
    const [botSpeaking, setBotSpeaking] = useState(false);
    const [muted, setMuted] = useState(false);
    const [showTranscript, setShowTranscript] = useState(true);
    const [messages, setMessages] = useState<TranscriptMsg[]>([]);

    const audioRef = useRef<HTMLAudioElement | null>(null);
    const pcRef = useRef<RTCPeerConnection | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const streamRef = useRef<MediaStream | null>(null);
    const transcriptEndRef = useRef<HTMLDivElement | null>(null);
    // startCall is idempotent per run — a second attempt would be rejected with
    // "Workflow run already has an active call".
    const callStartedRef = useRef(false);
    const callActiveRef = useRef(false);

    // ── Init the embed session on mount ──
    useEffect(() => {
        if (!token) return;
        let cancelled = false;
        (async () => {
            try {
                const res = await fetch(`${backendUrl}/api/v1/public/embed/init`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ token }),
                });
                if (!res.ok) throw new Error(`init HTTP ${res.status}`);
                const data: InitResponse = await res.json();
                if (!cancelled) setInit(data);
            } catch (e) {
                logger.error(`Embed init failed: ${e}`);
                if (!cancelled) setError('This avatar is unavailable.');
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [token, backendUrl]);

    // On phones, keep the default view clean (avatar + controls only) — the
    // transcript is opened on demand via the toggle. On desktop it stays open.
    useEffect(() => {
        if (typeof window !== 'undefined' && window.matchMedia('(max-width: 639px)').matches) {
            setShowTranscript(false);
        }
    }, []);

    // Auto-scroll the transcript to the newest line.
    useEffect(() => {
        transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages]);

    // Append a bot-text fragment (concatenated) to the transcript.
    const appendBotText = useCallback((text: string) => {
        setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last && last.role === 'bot' && !last.final) {
                return [...prev.slice(0, -1), { ...last, text: `${last.text} ${text}`.trim() }];
            }
            return [...prev, { id: `bot-${prev.length}-${text.length}`, role: 'bot', text, final: false }];
        });
    }, []);

    // Add/replace the (interim) user transcription; finalize any open bot line.
    const setUserTranscription = useCallback((text: string, final: boolean) => {
        setMessages((prev) => {
            const finalizedBot = prev.map((m, i) =>
                i === prev.length - 1 && m.role === 'bot' && !m.final ? { ...m, final: true } : m
            );
            const withoutInterimUser = finalizedBot.filter((m) => !(m.role === 'user' && !m.final));
            return [...withoutInterimUser, { id: `user-${prev.length}`, role: 'user', text, final }];
        });
    }, []);

    // ── Public WebRTC call, started by the panel's "Start call" gesture ──
    const startCall = useCallback(async () => {
        if (!init) return;
        if (callStartedRef.current) return;
        callStartedRef.current = true;
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            streamRef.current = stream;

            // The signaling backend keys every peer connection by a client-supplied
            // pc_id and rejects offers/candidates that omit it ("Missing offer
            // fields"). Generate one per call, matching the main app's client.
            const pcId =
                'PC-' +
                Array.from(crypto.getRandomValues(new Uint8Array(16)))
                    .map((b) => b.toString(16).padStart(2, '0'))
                    .join('');

            const iceServers: RTCIceServer[] = [{ urls: 'stun:stun.l.google.com:19302' }];
            if (init.config?.turn_enabled) {
                try {
                    const t = await fetch(
                        `${backendUrl}/api/v1/public/embed/turn-credentials/${init.session_token}`
                    );
                    if (t.ok) {
                        const c = await t.json();
                        iceServers.push({ urls: c.uris, username: c.username, credential: c.password });
                    }
                } catch { /* fall back to STUN */ }
            }

            const pc = new RTCPeerConnection({
                iceServers,
                iceTransportPolicy: init.config?.force_turn_relay ? 'relay' : 'all',
            });
            pcRef.current = pc;
            stream.getTracks().forEach((tr) => pc.addTrack(tr, stream));
            pc.addEventListener('track', (evt) => {
                if (evt.track.kind === 'audio' && audioRef.current) {
                    audioRef.current.srcObject = evt.streams[0];
                }
            });

            const wsUrl =
                backendUrl.replace(/^http/, 'ws') +
                `/api/v1/ws/public/signaling/${init.session_token}`;
            const ws = new WebSocket(wsUrl);
            wsRef.current = ws;

            ws.onmessage = async (event) => {
                const msg = JSON.parse(event.data);
                switch (msg.type) {
                    case 'answer':
                        if (pcRef.current) {
                            await pcRef.current.setRemoteDescription({ type: 'answer', sdp: msg.payload.sdp });
                            callActiveRef.current = true;
                            setCallActive(true);
                        }
                        break;
                    case 'ice-candidate':
                        if (msg.payload.candidate && pcRef.current) {
                            try { await pcRef.current.addIceCandidate(msg.payload.candidate); } catch { /* ignore */ }
                        }
                        break;
                    case 'rtf-bot-started-speaking':
                        setBotSpeaking(true);
                        break;
                    case 'rtf-bot-stopped-speaking':
                        setBotSpeaking(false);
                        break;
                    case 'rtf-bot-text':
                        if (msg.payload?.text) appendBotText(msg.payload.text);
                        break;
                    case 'rtf-user-transcription':
                        if (msg.payload?.text) setUserTranscription(msg.payload.text, !!msg.payload.final);
                        break;
                    case 'call-ended':
                        callActiveRef.current = false;
                        setCallActive(false);
                        setCallEnded(true);
                        break;
                    case 'error': {
                        const detail = msg.payload?.message || 'connection error';
                        logger.error(`Embed signaling error: ${detail}`);
                        if (!callActiveRef.current) setError(`Could not start the call: ${detail}`);
                        break;
                    }
                }
            };
            ws.onclose = () => {
                callActiveRef.current = false;
                callStartedRef.current = false;
                setCallActive(false);
            };

            await new Promise<void>((resolve, reject) => {
                ws.onopen = () => resolve();
                ws.onerror = () => reject(new Error('signaling failed'));
            });

            pc.onicecandidate = (e) => {
                if (e.candidate && ws.readyState === WebSocket.OPEN) {
                    ws.send(
                        JSON.stringify({
                            type: 'ice-candidate',
                            payload: {
                                candidate: {
                                    candidate: e.candidate.candidate,
                                    sdpMid: e.candidate.sdpMid,
                                    sdpMLineIndex: e.candidate.sdpMLineIndex,
                                },
                                pc_id: pcId,
                            },
                        })
                    );
                }
            };
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);
            ws.send(
                JSON.stringify({
                    type: 'offer',
                    payload: { sdp: offer.sdp, type: offer.type, pc_id: pcId },
                })
            );
        } catch (e) {
            callStartedRef.current = false;
            logger.error(`Embed call failed: ${e}`);
            setError('Microphone access is required to talk to the avatar.');
        }
    }, [init, backendUrl, appendBotText, setUserTranscription]);

    const toggleMute = useCallback(() => {
        const next = !muted;
        setMuted(next);
        streamRef.current?.getAudioTracks().forEach((t) => { t.enabled = !next; });
    }, [muted]);

    const endCall = useCallback(() => {
        try { wsRef.current?.close(); } catch { /* noop */ }
        try { pcRef.current?.close(); } catch { /* noop */ }
        try { streamRef.current?.getTracks().forEach((t) => t.stop()); } catch { /* noop */ }
        callActiveRef.current = false;
        setCallActive(false);
        setBotSpeaking(false);
        setCallEnded(true);
    }, []);

    useEffect(() => {
        return () => {
            try { wsRef.current?.close(); } catch { /* noop */ }
            try { pcRef.current?.close(); } catch { /* noop */ }
            try { streamRef.current?.getTracks().forEach((t) => t.stop()); } catch { /* noop */ }
        };
    }, []);

    return (
        <div className="relative h-[100dvh] w-full overflow-hidden bg-[#0a0812]">
            {/* Full-bleed avatar background */}
            {error ? (
                <div className="flex h-full w-full items-center justify-center px-6 text-center text-sm text-white/70">
                    {error}
                </div>
            ) : init ? (
                // Desktop: full-bleed. Mobile: the avatar is inset with equal
                // left/right margins (a centered rounded card) per request.
                <div className="absolute inset-0 max-sm:px-5">
                    <div className="h-full w-full overflow-hidden max-sm:rounded-3xl">
                        <SpatialAvatarPanel
                            accessToken={null}
                            publicSessionToken={init.session_token}
                            active={callActive}
                            botSpeaking={botSpeaking}
                            audioRef={audioRef}
                            workflowRunId={init.workflow_run_id}
                            onRequestCallStart={startCall}
                            fullBleed
                        />
                    </div>
                </div>
            ) : (
                <div className="flex h-full w-full items-center justify-center">
                    <Loader2 className="h-8 w-8 animate-spin text-white/50" />
                </div>
            )}

            {/* Live Transcript panel */}
            {showTranscript && callActive ? (
                <div className="absolute right-4 top-4 z-20 flex max-h-[70vh] w-80 max-w-[85vw] flex-col overflow-hidden rounded-2xl border border-white/10 bg-black/45 backdrop-blur-md max-sm:inset-x-2 max-sm:bottom-24 max-sm:top-auto max-sm:h-auto max-sm:w-auto max-sm:max-w-none max-sm:max-h-[46vh]">
                    <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
                        <div className="flex items-center gap-2 text-sm font-semibold text-white">
                            <MessageSquare className="h-4 w-4" /> Live Transcript
                        </div>
                        <button
                            type="button"
                            onClick={() => setShowTranscript(false)}
                            className="text-white/60 hover:text-white"
                            aria-label="Hide transcript"
                        >
                            <X className="h-4 w-4" />
                        </button>
                    </div>
                    <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
                        {messages.length === 0 ? (
                            <p className="text-xs text-white/40">Listening…</p>
                        ) : (
                            messages.map((m) => (
                                <div key={m.id} className={m.role === 'user' ? 'text-right' : 'text-left'}>
                                    <span
                                        className={
                                            'inline-block rounded-2xl px-3 py-2 text-sm ' +
                                            (m.role === 'user'
                                                ? 'bg-[#6d4aff]/80 text-white'
                                                : 'bg-white/10 text-white/90')
                                        }
                                    >
                                        {m.text}
                                    </span>
                                </div>
                            ))
                        )}
                        <div ref={transcriptEndRef} />
                    </div>
                </div>
            ) : null}

            {/* Show-transcript pill when hidden */}
            {!showTranscript && callActive ? (
                <button
                    type="button"
                    onClick={() => setShowTranscript(true)}
                    className="absolute right-4 top-4 z-20 flex items-center gap-2 rounded-full border border-white/10 bg-black/45 px-4 py-2 text-sm font-medium text-white backdrop-blur-md hover:bg-black/60 max-sm:right-2 max-sm:top-2"
                >
                    <MessageSquare className="h-4 w-4" /> Transcript
                </button>
            ) : null}

            {/* Bottom control bar (only during an active call) */}
            {callActive && !callEnded ? (
                <div className="absolute bottom-6 left-1/2 z-20 flex -translate-x-1/2 items-center gap-3 rounded-full border border-white/10 bg-black/45 px-4 py-3 backdrop-blur-md max-sm:bottom-4 max-sm:max-w-[95vw] max-sm:gap-2 max-sm:px-3 max-sm:py-2">
                    <button
                        type="button"
                        onClick={toggleMute}
                        className={
                            'flex h-12 w-12 items-center justify-center rounded-full transition ' +
                            (muted ? 'bg-white/15 text-white' : 'bg-white/10 text-white hover:bg-white/20')
                        }
                        aria-label={muted ? 'Unmute microphone' : 'Mute microphone'}
                        title={muted ? 'Unmute' : 'Mute'}
                    >
                        {muted ? <MicOff className="h-5 w-5" /> : <Mic className="h-5 w-5" />}
                    </button>
                    <button
                        type="button"
                        onClick={endCall}
                        className="flex h-12 items-center gap-2 whitespace-nowrap rounded-full bg-red-500 px-6 font-semibold text-white transition hover:bg-red-600 max-sm:px-4"
                        aria-label="End call"
                    >
                        <PhoneOff className="h-5 w-5" /> End call
                    </button>
                </div>
            ) : null}

            {/* Call-ended overlay with restart */}
            {callEnded ? (
                <div className="absolute inset-0 z-30 flex flex-col items-center justify-center gap-5 bg-black/70 backdrop-blur-sm">
                    <p className="text-lg font-semibold text-white">Call ended</p>
                    <button
                        type="button"
                        onClick={() => window.location.reload()}
                        className="rounded-full bg-gradient-to-b from-[#8b6dff] to-[#6d4aff] px-8 py-3 font-semibold text-white shadow-lg shadow-purple-900/40 transition hover:brightness-110"
                    >
                        Start a new call
                    </button>
                </div>
            ) : null}

            <audio ref={audioRef} autoPlay playsInline className="hidden" />
        </div>
    );
}
