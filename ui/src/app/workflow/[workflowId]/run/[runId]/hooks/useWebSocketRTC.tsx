import { useCallback, useEffect, useRef, useState } from "react";

import { client } from "@/client/client.gen";
import { validateUserConfigurationsApiV1UserConfigurationsUserValidateGet, validateWorkflowApiV1WorkflowWorkflowIdValidatePost } from "@/client/sdk.gen";
import { WorkflowValidationError } from "@/components/flow/types";
import logger from '@/lib/logger';

import { sdpFilterCodec } from "../utils";
import { useDeviceInputs } from "./useDeviceInputs";

interface UseWebSocketRTCProps {
    workflowId: number;
    workflowRunId: number;
    accessToken: string | null;
    initialContextVariables?: Record<string, string> | null;
}

export const useWebSocketRTC = ({ workflowId, workflowRunId, accessToken, initialContextVariables }: UseWebSocketRTCProps) => {
    const [connectionStatus, setConnectionStatus] = useState<'idle' | 'connecting' | 'connected' | 'failed'>('idle');
    const [connectionActive, setConnectionActive] = useState(false);
    const [isCompleted, setIsCompleted] = useState(false);
    const [apiKeyModalOpen, setApiKeyModalOpen] = useState(false);
    const [apiKeyError, setApiKeyError] = useState<string | null>(null);
    const [workflowConfigModalOpen, setWorkflowConfigModalOpen] = useState(false);
    const [workflowConfigError, setWorkflowConfigError] = useState<string | null>(null);
    const [isStarting, setIsStarting] = useState(false);
    const initialContext = initialContextVariables || {};

    const {
        audioInputs,
        selectedAudioInput,
        setSelectedAudioInput,
        permissionError,
        setPermissionError,
        getAudioInputDevices
    } = useDeviceInputs();

    const useStun = true;
    const useAudio = true;
    const audioCodec = 'default';

    const audioRef = useRef<HTMLAudioElement>(null);
    const pcRef = useRef<RTCPeerConnection | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const timeStartRef = useRef<number | null>(null);

    // Generate a cryptographically secure unique ID
    const generateSecureId = () => {
        // Use Web Crypto API to generate random bytes
        const array = new Uint8Array(16);
        crypto.getRandomValues(array);
        // Convert to hex string
        return 'PC-' + Array.from(array)
            .map(b => b.toString(16).padStart(2, '0'))
            .join('');
    };

    const pc_id = useRef(generateSecureId());

    // Get WebSocket URL from client configuration
    const getWebSocketUrl = useCallback(() => {
        // Get base URL from client configuration
        const baseUrl = client.getConfig().baseUrl || 'http://127.0.0.1:8000';
        // Convert HTTP to WS protocol
        const wsUrl = baseUrl.replace(/^http/, 'ws');
        return `${wsUrl}/api/v1/ws/signaling/${workflowId}/${workflowRunId}?token=${accessToken}`;
    }, [workflowId, workflowRunId, accessToken]);

    const createPeerConnection = () => {
        const config: RTCConfiguration = {
            iceServers: useStun ? [{ urls: ['stun:stun.l.google.com:19302'] }] : []
        };

        const pc = new RTCPeerConnection(config);

        // Set up ICE candidate trickling
        pc.addEventListener('icecandidate', (event) => {
            if (wsRef.current?.readyState === WebSocket.OPEN) {
                const message = {
                    type: 'ice-candidate',
                    payload: {
                        candidate: event.candidate ? {
                            candidate: event.candidate.candidate,
                            sdpMid: event.candidate.sdpMid,
                            sdpMLineIndex: event.candidate.sdpMLineIndex
                        } : null,
                        pc_id: pc_id.current
                    }
                };
                wsRef.current.send(JSON.stringify(message));

                if (event.candidate) {
                    logger.debug(`Sending ICE candidate: ${event.candidate.candidate}`);
                } else {
                    logger.debug('Sending end-of-candidates signal');
                }
            }
        });

        pc.addEventListener('iceconnectionstatechange', () => {
            logger.info(`ICE connection state changed: ${pc.iceConnectionState}`);
            if (pc.iceConnectionState === 'connected' || pc.iceConnectionState === 'completed') {
                setConnectionStatus('connected');
            } else if (pc.iceConnectionState === 'failed') {
                setConnectionStatus('failed');
            } else if (pc.iceConnectionState === 'disconnected') {
                // Server-initiated disconnect - clean up gracefully
                logger.info('Server initiated disconnect - cleaning up connection');

                // Close WebSocket if still open
                if (wsRef.current) {
                    wsRef.current.close();
                    wsRef.current = null;
                }

                // Mark as completed to trigger recording check
                setConnectionActive(false);
                setIsCompleted(true);
                setConnectionStatus('idle');

                // Clean up peer connection
                if (pc.getTransceivers) {
                    pc.getTransceivers().forEach((transceiver) => {
                        if (transceiver.stop) {
                            transceiver.stop();
                        }
                    });
                }

                pc.getSenders().forEach((sender) => {
                    if (sender.track) {
                        sender.track.stop();
                    }
                });
            }
        });

        pc.addEventListener('track', (evt) => {
            if (evt.track.kind === 'audio' && audioRef.current) {
                audioRef.current.srcObject = evt.streams[0];
            }
        });

        pcRef.current = pc;
        return pc;
    };

    const connectWebSocket = useCallback(() => {
        return new Promise<void>((resolve, reject) => {
            const wsUrl = getWebSocketUrl();
            logger.info(`Connecting to WebSocket: ${wsUrl}`);

            const ws = new WebSocket(wsUrl);

            ws.onopen = () => {
                logger.info('WebSocket connected');
                wsRef.current = ws;
                resolve();
            };

            ws.onerror = (error) => {
                logger.error('WebSocket error:', error);
                reject(error);
            };

            ws.onclose = () => {
                logger.info('WebSocket closed');
                wsRef.current = null;
                // Don't set failed status if already completed (graceful disconnect)
                if (connectionActive && !isCompleted) {
                    setConnectionStatus('failed');
                }
            };

            ws.onmessage = async (event) => {
                try {
                    const message = JSON.parse(event.data);

                    switch (message.type) {
                        case 'answer':
                            // Set remote description immediately (may have no candidates)
                            const answer = message.payload;
                            logger.debug('Received answer from server');

                            if (pcRef.current) {
                                await pcRef.current.setRemoteDescription({
                                    type: 'answer',
                                    sdp: answer.sdp
                                });
                                setConnectionActive(true);
                                logger.info('Remote description set');
                            }
                            break;

                        case 'ice-candidate':
                            // Add ICE candidate from server
                            const candidate = message.payload.candidate;

                            if (candidate && pcRef.current) {
                                try {
                                    await pcRef.current.addIceCandidate({
                                        candidate: candidate.candidate,
                                        sdpMid: candidate.sdpMid,
                                        sdpMLineIndex: candidate.sdpMLineIndex
                                    });
                                    logger.debug(`Added remote ICE candidate: ${candidate.candidate}`);
                                } catch (e) {
                                    logger.error('Failed to add ICE candidate:', e);
                                }
                            } else if (!candidate) {
                                logger.debug('Received end-of-candidates signal from server');
                            }
                            break;

                        case 'error':
                            logger.error('Server error:', message.payload);
                            break;

                        default:
                            logger.warn('Unknown message type:', message.type);
                    }
                } catch (e) {
                    logger.error('Failed to handle WebSocket message:', e);
                }
            };
        });
    }, [getWebSocketUrl, connectionActive, isCompleted]);

    const negotiate = async () => {
        const pc = pcRef.current;
        const ws = wsRef.current;

        if (!pc || !ws || ws.readyState !== WebSocket.OPEN) {
            logger.error('Cannot negotiate: PC or WebSocket not ready');
            return;
        }

        try {
            // Create offer
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);

            const localDescription = pc.localDescription;
            if (!localDescription) return;

            let sdp = localDescription.sdp;

            if (audioCodec !== 'default') {
                sdp = sdpFilterCodec('audio', audioCodec, sdp);
            }

            // Send offer immediately via WebSocket (without waiting for ICE gathering)
            const message = {
                type: 'offer',
                payload: {
                    sdp: sdp,
                    type: 'offer',
                    pc_id: pc_id.current,
                    workflow_id: workflowId,
                    workflow_run_id: workflowRunId,
                    call_context_vars: initialContext
                }
            };

            ws.send(JSON.stringify(message));
            logger.info('Sent offer via WebSocket (ICE trickling enabled)');

        } catch (e) {
            logger.error(`Negotiation failed: ${e}`);
            setConnectionStatus('failed');
        }
    };

    const start = async () => {
        if (isStarting || !accessToken) return;
        setIsStarting(true);
        setConnectionStatus('connecting');

        try {
            // Validate API keys
            const response = await validateUserConfigurationsApiV1UserConfigurationsUserValidateGet({
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
                query: {
                    validity_ttl_seconds: 86400
                },
            });

            if (response.error) {
                setApiKeyModalOpen(true);
                let msg = 'API Key Error';
                const detail = (response.error as unknown as { detail?: { errors: { model: string; message: string }[] } }).detail;
                if (Array.isArray(detail)) {
                    msg = detail
                        .map((e: { model: string; message: string }) => `${e.model}: ${e.message}`)
                        .join('\n');
                }
                setApiKeyError(msg);
                setConnectionStatus('failed');
                return;
            }

            // Validate workflow
            const workflowResponse = await validateWorkflowApiV1WorkflowWorkflowIdValidatePost({
                path: {
                    workflow_id: workflowId,
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
            });

            if (workflowResponse.error) {
                setWorkflowConfigModalOpen(true);
                let msg = 'Workflow validation failed';
                const errorDetail = workflowResponse.error as { detail?: { errors: WorkflowValidationError[] } };
                if (errorDetail?.detail?.errors) {
                    msg = errorDetail.detail.errors
                        .map(err => `${err.kind}: ${err.message}`)
                        .join('\n');
                }
                setWorkflowConfigError(msg);
                setConnectionStatus('failed');
                return;
            }

            // Connect WebSocket first
            await connectWebSocket();

            // Create peer connection
            timeStartRef.current = null;
            const pc = createPeerConnection();

            // Set up media constraints
            const constraints: MediaStreamConstraints = {
                audio: false,
            };

            if (useAudio) {
                const audioConstraints: MediaTrackConstraints = {};
                if (selectedAudioInput) {
                    audioConstraints.deviceId = { exact: selectedAudioInput };
                }
                constraints.audio = Object.keys(audioConstraints).length ? audioConstraints : true;
            }

            // Get user media and negotiate
            if (constraints.audio) {
                try {
                    const stream = await navigator.mediaDevices.getUserMedia(constraints);
                    stream.getTracks().forEach((track) => {
                        pc.addTrack(track, stream);
                    });
                    await negotiate();
                } catch (err) {
                    logger.error(`Could not acquire media: ${err}`);
                    setPermissionError('Could not acquire media');
                    setConnectionStatus('failed');
                }
            } else {
                await negotiate();
            }
        } catch (error) {
            logger.error('Failed to start connection:', error);
            setConnectionStatus('failed');
        } finally {
            setIsStarting(false);
        }
    };

    const stop = () => {
        setConnectionActive(false);
        setIsCompleted(true);
        setConnectionStatus('idle');

        // Close WebSocket
        if (wsRef.current) {
            wsRef.current.close();
            wsRef.current = null;
        }

        // Close peer connection
        const pc = pcRef.current;
        if (!pc) return;

        if (pc.getTransceivers) {
            pc.getTransceivers().forEach((transceiver) => {
                if (transceiver.stop) {
                    transceiver.stop();
                }
            });
        }

        pc.getSenders().forEach((sender) => {
            if (sender.track) {
                sender.track.stop();
            }
        });

        setTimeout(() => {
            if (pcRef.current) {
                pcRef.current.close();
                pcRef.current = null;
            }
        }, 500);
    };

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            if (wsRef.current) {
                wsRef.current.close();
            }
            if (pcRef.current) {
                pcRef.current.close();
            }
        };
    }, []);

    return {
        audioRef,
        audioInputs,
        selectedAudioInput,
        setSelectedAudioInput,
        connectionActive,
        permissionError,
        isCompleted,
        apiKeyModalOpen,
        setApiKeyModalOpen,
        apiKeyError,
        workflowConfigError,
        workflowConfigModalOpen,
        setWorkflowConfigModalOpen,
        connectionStatus,
        start,
        stop,
        isStarting,
        initialContext,
        getAudioInputDevices
    };
};
