'use client';

import { Pause, Play, Volume2, VolumeX } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useAppConfig } from '@/context/AppConfigContext';
import { useAuth } from '@/lib/auth';
import logger from '@/lib/logger';

interface LiveAudioPlayerProps {
    testSessionId: number;
    sessionStatus: 'pending' | 'running' | 'completed' | 'failed';
    autoStart?: boolean;
}

type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error';
type AudioRole = 'mixed' | 'actor' | 'adversary';

export function LiveAudioPlayer({
    testSessionId,
    sessionStatus,
    autoStart = false
}: LiveAudioPlayerProps) {
    const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('disconnected');
    const [audioRole, setAudioRole] = useState<AudioRole>(() => {
        // Load saved preference from localStorage
        if (typeof window !== 'undefined') {
            const saved = localStorage.getItem('looptalk-audio-role');
            return (saved as AudioRole) || 'mixed';
        }
        return 'mixed';
    });
    const [isPlaying, setIsPlaying] = useState(false);
    const [volume, setVolume] = useState(0.8);
    const [bufferedDuration, setBufferedDuration] = useState(0);
    const [audioLevel, setAudioLevel] = useState(0);

    const wsRef = useRef<WebSocket | null>(null);
    const audioContextRef = useRef<AudioContext | null>(null);
    const gainNodeRef = useRef<GainNode | null>(null);
    const analyserRef = useRef<AnalyserNode | null>(null);
    const audioQueueRef = useRef<AudioBufferSourceNode[]>([]);
    const nextStartTimeRef = useRef(0);
    const animationFrameRef = useRef<number | undefined>(undefined);
    const isConnectingRef = useRef(false);
    const { config } = useAppConfig();
    const { user, getAccessToken } = useAuth();

    // Auto-start streaming when session starts
    useEffect(() => {
        if (sessionStatus === 'running' && autoStart && !isPlaying) {
            setIsPlaying(true);
        }
    }, [sessionStatus, autoStart, isPlaying]);

    // Save audio role preference
    useEffect(() => {
        if (typeof window !== 'undefined') {
            localStorage.setItem('looptalk-audio-role', audioRole);
        }
    }, [audioRole]);

    // Audio level monitoring
    const monitorAudioLevel = useCallback(() => {
        if (!analyserRef.current) return;

        const dataArray = new Uint8Array(analyserRef.current.frequencyBinCount);
        analyserRef.current.getByteFrequencyData(dataArray);

        // Calculate average level
        const average = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;
        setAudioLevel(average / 255); // Normalize to 0-1

        animationFrameRef.current = requestAnimationFrame(monitorAudioLevel);
    }, []);

    const connectWebSocket = useCallback(async () => {
        // Check if already connected or connecting
        if (wsRef.current && (wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING)) {
            logger.debug('WebSocket already connected or connecting, skipping');
            return;
        }

        // Prevent multiple concurrent connection attempts
        if (isConnectingRef.current) {
            logger.debug('Already attempting to connect, skipping');
            return;
        }

        isConnectingRef.current = true;

        try {
            setConnectionStatus('connecting');

            if (!user) return;
            // Get auth token
            const accessToken = await getAccessToken();

            // Create WebSocket connection
            const baseUrl = (config?.backendApiEndpoint || 'http://localhost:8000').replace(/^http/, 'ws');
            const wsUrl = `${baseUrl}/api/v1/looptalk/test-sessions/${testSessionId}/audio-stream?role=${audioRole}&token=${encodeURIComponent(accessToken || '')}`;
            const ws = new WebSocket(wsUrl);
            wsRef.current = ws;

            // Create AudioContext with gain control and analyser
            if (!audioContextRef.current) {
                audioContextRef.current = new AudioContext();
                gainNodeRef.current = audioContextRef.current.createGain();
                analyserRef.current = audioContextRef.current.createAnalyser();
                analyserRef.current.fftSize = 256;

                // Connect gain -> analyser -> destination
                gainNodeRef.current.connect(analyserRef.current);
                analyserRef.current.connect(audioContextRef.current.destination);

                // Set initial volume
                gainNodeRef.current.gain.value = volume;
            }

            ws.onopen = () => {
                setConnectionStatus('connected');
                logger.info('Audio stream connected');
                monitorAudioLevel();
            };

            ws.onmessage = async (event) => {
                try {
                    const data = JSON.parse(event.data);

                    if (data.type === 'audio' && data.audio) {
                        // Decode base64 audio data
                        const audioBytes = Uint8Array.from(atob(data.audio), c => c.charCodeAt(0));

                        // Create audio buffer from PCM data
                        const samplesPerChannel = audioBytes.length / (data.num_channels * 2);
                        const audioBuffer = audioContextRef.current!.createBuffer(
                            data.num_channels,
                            samplesPerChannel,
                            data.sample_rate
                        );

                        // Convert PCM to float samples
                        const dataView = new DataView(audioBytes.buffer);
                        for (let channel = 0; channel < data.num_channels; channel++) {
                            const channelData = audioBuffer.getChannelData(channel);
                            for (let i = 0; i < samplesPerChannel; i++) {
                                const sampleIndex = i * data.num_channels + channel;
                                const sample = dataView.getInt16(sampleIndex * 2, true) / 32768.0;
                                channelData[i] = sample;
                            }
                        }

                        // Schedule audio buffer playback
                        const source = audioContextRef.current!.createBufferSource();
                        source.buffer = audioBuffer;
                        source.connect(gainNodeRef.current!);

                        // Schedule seamless playback
                        const currentTime = audioContextRef.current!.currentTime;
                        if (nextStartTimeRef.current < currentTime) {
                            nextStartTimeRef.current = currentTime;
                        }
                        source.start(nextStartTimeRef.current);
                        nextStartTimeRef.current += audioBuffer.duration;

                        // Track scheduled sources
                        audioQueueRef.current.push(source);
                        source.onended = () => {
                            const index = audioQueueRef.current.indexOf(source);
                            if (index > -1) {
                                audioQueueRef.current.splice(index, 1);
                            }
                        };

                        setBufferedDuration(nextStartTimeRef.current - currentTime);
                    }
                } catch (error) {
                    logger.error('Error processing audio data:', error);
                }
            };

            ws.onerror = (error) => {
                logger.error('WebSocket error:', error);
                setConnectionStatus('error');
            };

            ws.onclose = (event) => {
                setConnectionStatus('disconnected');
                logger.info('Audio stream disconnected', { code: event.code, reason: event.reason });
                if (animationFrameRef.current) {
                    cancelAnimationFrame(animationFrameRef.current);
                }
            };

        } catch (error) {
            logger.error('Error connecting to audio stream:', error);
            setConnectionStatus('error');
        } finally {
            isConnectingRef.current = false;
        }
    }, [testSessionId, audioRole, user, getAccessToken, volume, monitorAudioLevel, config]); // Removed connectionStatus to avoid loops

    const disconnect = useCallback(() => {
        if (wsRef.current) {
            wsRef.current.close();
            wsRef.current = null;
        }

        // Stop all scheduled audio
        audioQueueRef.current.forEach(source => {
            try {
                source.stop();
            } catch {
                // Ignore if already stopped
            }
        });
        audioQueueRef.current = [];
        nextStartTimeRef.current = 0;
        setBufferedDuration(0);
        setAudioLevel(0);

        if (animationFrameRef.current) {
            cancelAnimationFrame(animationFrameRef.current);
        }
    }, []);

    // Handle play/pause
    useEffect(() => {
        if (isPlaying && sessionStatus === 'running') {
            connectWebSocket();
        } else {
            disconnect();
        }

        return () => {
            disconnect();
        };
    }, [isPlaying, sessionStatus, connectWebSocket, disconnect]); // Include stable callbacks

    // Handle audio role changes
    useEffect(() => {
        // Use ref to check connection state to avoid dependency issues
        if (isPlaying && wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            logger.info('Audio role changed, reconnecting with new role:', audioRole);
            // Reconnect with new role
            disconnect();
            // Set a flag to prevent double connections
            const timer = setTimeout(() => {
                if (isPlaying) {
                    connectWebSocket();
                }
            }, 500);
            return () => clearTimeout(timer);
        }
    }, [audioRole, isPlaying, connectWebSocket, disconnect]); // Include all dependencies

    // Update volume
    useEffect(() => {
        if (gainNodeRef.current) {
            gainNodeRef.current.gain.value = volume;
        }
    }, [volume]);


    const getStatusColor = () => {
        switch (connectionStatus) {
            case 'connected': return 'bg-green-500';
            case 'connecting': return 'bg-yellow-500';
            case 'error': return 'bg-red-500';
            default: return 'bg-gray-500';
        }
    };

    return (
        <Card>
            <CardHeader>
                <div className="flex items-center justify-between">
                    <CardTitle className="text-lg flex items-center gap-2">
                        <Volume2 className="h-5 w-5" />
                        Live Audio Stream
                    </CardTitle>
                    <div className="flex items-center gap-2">
                        <div className={`w-2 h-2 rounded-full ${getStatusColor()}`} />
                        <Badge variant={connectionStatus === 'connected' ? 'default' : 'secondary'}>
                            {connectionStatus}
                        </Badge>
                    </div>
                </div>
            </CardHeader>
            <CardContent className="space-y-4">
                {/* Play/Pause Controls */}
                <div className="flex items-center gap-4">
                    <Button
                        onClick={() => setIsPlaying(!isPlaying)}
                        disabled={sessionStatus !== 'running'}
                        size="sm"
                        variant={isPlaying ? 'default' : 'outline'}
                    >
                        {isPlaying ? (
                            <>
                                <Pause className="h-4 w-4 mr-2" />
                                Pause
                            </>
                        ) : (
                            <>
                                <Play className="h-4 w-4 mr-2" />
                                Play
                            </>
                        )}
                    </Button>

                    {/* Audio Role Selector */}
                    <div className="flex gap-1">
                        {(['mixed', 'actor', 'adversary'] as const).map((role) => (
                            <Button
                                key={role}
                                size="sm"
                                variant={audioRole === role ? 'default' : 'outline'}
                                onClick={() => setAudioRole(role)}
                                className="capitalize"
                            >
                                {role}
                            </Button>
                        ))}
                    </div>
                </div>

                {/* Volume Control */}
                <div className="flex items-center gap-4">
                    <VolumeX className="h-4 w-4 text-gray-500" />
                    <input
                        type="range"
                        value={volume}
                        onChange={(e) => setVolume(parseFloat(e.target.value))}
                        min="0"
                        max="1"
                        step="0.01"
                        className="flex-1 h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer"
                    />
                    <Volume2 className="h-4 w-4 text-gray-500" />
                </div>

                {/* Audio Level Meter */}
                <div className="space-y-2">
                    <div className="text-sm text-gray-500">Audio Level</div>
                    <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                        <div
                            className="h-full bg-green-500 transition-all duration-100"
                            style={{ width: `${audioLevel * 100}%` }}
                        />
                    </div>
                </div>

                {/* Status Info */}
                <div className="text-sm text-gray-500">
                    {connectionStatus === 'connected' && (
                        <>Streaming... (buffered: {bufferedDuration.toFixed(1)}s)</>
                    )}
                    {connectionStatus === 'connecting' && 'Connecting to audio stream...'}
                    {connectionStatus === 'error' && 'Failed to connect to audio stream'}
                    {connectionStatus === 'disconnected' && sessionStatus === 'running' && 'Click play to start streaming'}
                    {sessionStatus === 'pending' && 'Waiting for session to start...'}
                    {sessionStatus === 'completed' && 'Session completed'}
                    {sessionStatus === 'failed' && 'Session failed'}
                </div>
            </CardContent>
        </Card>
    );
}
