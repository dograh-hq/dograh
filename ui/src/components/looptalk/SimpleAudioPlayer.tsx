'use client';

import { Volume2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { useAuth } from '@/lib/auth';
import logger from '@/lib/logger';
import { getWebSocketUrl } from '@/lib/backend-url';

interface SimpleAudioPlayerProps {
    testSessionId: number;
}

export function SimpleAudioPlayer({ testSessionId }: SimpleAudioPlayerProps) {
    const [connectionStatus, setConnectionStatus] = useState<'connecting' | 'connected' | 'error'>('connecting');
    const [audioRole, setAudioRole] = useState<'mixed' | 'actor' | 'adversary'>('mixed');
    const wsRef = useRef<WebSocket | null>(null);
    const audioContextRef = useRef<AudioContext | null>(null);
    const [bufferedDuration, setBufferedDuration] = useState(0);
    const { user, getAccessToken } = useAuth();
    const audioQueueRef = useRef<AudioBufferSourceNode[]>([]);
    const nextStartTimeRef = useRef(0);

    useEffect(() => {
        const connectWebSocket = async () => {
            try {
                if (!user) return;
                // Get auth token
                const accessToken = await getAccessToken();

                // Create WebSocket connection using shared utility
                const baseUrl = getWebSocketUrl();
                const wsUrl = `${baseUrl}/api/v1/looptalk/test-sessions/${testSessionId}/audio-stream?role=${audioRole}&token=${encodeURIComponent(accessToken || '')}`;
                const ws = new WebSocket(wsUrl);
                wsRef.current = ws;

                // Create AudioContext
                audioContextRef.current = new AudioContext();

                ws.onopen = () => {
                    setConnectionStatus('connected');
                };

                ws.onmessage = async (event) => {
                    try {
                        const data = JSON.parse(event.data);

                        if (data.type === 'audio' && data.audio) {
                            // Decode base64 audio data
                            const audioBytes = Uint8Array.from(atob(data.audio), c => c.charCodeAt(0));

                            // Create audio buffer from PCM data
                            const samplesPerChannel = audioBytes.length / (data.num_channels * 2); // 16-bit samples
                            const audioBuffer = audioContextRef.current!.createBuffer(
                                data.num_channels,
                                samplesPerChannel,
                                data.sample_rate
                            );

                            // Convert PCM to float samples for each channel
                            const dataView = new DataView(audioBytes.buffer);
                            for (let channel = 0; channel < data.num_channels; channel++) {
                                const channelData = audioBuffer.getChannelData(channel);
                                for (let i = 0; i < samplesPerChannel; i++) {
                                    // Interleaved PCM data: L,R,L,R,... for stereo
                                    const sampleIndex = i * data.num_channels + channel;
                                    const sample = dataView.getInt16(sampleIndex * 2, true) / 32768.0;
                                    channelData[i] = sample;
                                }
                            }

                            // Schedule audio buffer playback
                            const source = audioContextRef.current!.createBufferSource();
                            source.buffer = audioBuffer;
                            source.connect(audioContextRef.current!.destination);

                            // Schedule seamless playback
                            const currentTime = audioContextRef.current!.currentTime;
                            if (nextStartTimeRef.current < currentTime) {
                                nextStartTimeRef.current = currentTime;
                            }
                            source.start(nextStartTimeRef.current);
                            nextStartTimeRef.current += audioBuffer.duration;

                            // Keep track of scheduled sources for cleanup
                            audioQueueRef.current.push(source);
                            source.onended = () => {
                                const index = audioQueueRef.current.indexOf(source);
                                if (index > -1) {
                                    audioQueueRef.current.splice(index, 1);
                                }
                            };

                            setBufferedDuration((prev: number) => prev + audioBuffer.duration);
                        } else if (data.type === 'keepalive') {
                            // Connection is alive
                        }
                    } catch (error) {
                        logger.error('Error processing audio data:', error);
                    }
                };

                ws.onerror = (error) => {
                    logger.error('WebSocket error:', error);
                    setConnectionStatus('error');
                };

                ws.onclose = () => {
                    setConnectionStatus('error');
                };

            } catch (error) {
                logger.error('Error connecting to audio stream:', error);
                setConnectionStatus('error');
            }
        };

        connectWebSocket();

        // Cleanup
        return () => {
            if (wsRef.current) {
                wsRef.current.close();
            }
            // Stop all scheduled audio
            audioQueueRef.current.forEach((source: AudioBufferSourceNode) => {
                try {
                    source.stop();
                } catch {
                    // Ignore if already stopped
                }
            });
            audioQueueRef.current = [];
            nextStartTimeRef.current = 0;
            setBufferedDuration(0);
            if (audioContextRef.current) {
                audioContextRef.current.close();
            }
        };
    }, [testSessionId, audioRole, user, getAccessToken]);

    return (
        <Card>
            <CardContent className="pt-4">
                <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center gap-2">
                        <Volume2 className="h-5 w-5 text-gray-600" />
                        <span className="font-medium">Live Audio Stream</span>
                    </div>
                    <Badge variant={connectionStatus === 'connected' ? 'default' : connectionStatus === 'error' ? 'destructive' : 'secondary'}>
                        {connectionStatus}
                    </Badge>
                </div>

                <div className="flex gap-2 mb-4">
                    <button
                        className={`px-3 py-1 rounded text-sm ${audioRole === 'mixed' ? 'bg-primary text-white' : 'bg-gray-200'}`}
                        onClick={() => setAudioRole('mixed')}
                    >
                        Mixed
                    </button>
                    <button
                        className={`px-3 py-1 rounded text-sm ${audioRole === 'actor' ? 'bg-primary text-white' : 'bg-gray-200'}`}
                        onClick={() => setAudioRole('actor')}
                    >
                        Actor Only
                    </button>
                    <button
                        className={`px-3 py-1 rounded text-sm ${audioRole === 'adversary' ? 'bg-primary text-white' : 'bg-gray-200'}`}
                        onClick={() => setAudioRole('adversary')}
                    >
                        Adversary Only
                    </button>
                </div>

                <div className="text-sm text-gray-500">
                    {connectionStatus === 'connected' && (
                        <>Audio streaming... (buffered: {bufferedDuration.toFixed(1)}s)</>
                    )}
                    {connectionStatus === 'connecting' && 'Connecting to audio stream...'}
                    {connectionStatus === 'error' && 'Failed to connect to audio stream'}
                </div>
            </CardContent>
        </Card>
    );
}
