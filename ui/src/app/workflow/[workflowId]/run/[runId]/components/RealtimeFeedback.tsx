'use client';

import { FeedbackMessage } from '../hooks/useWebSocketRTC';
import { processLiveMessages, processTranscriptEvents, TranscriptEvent } from '../utils/processTranscriptEvents';
import { UnifiedTranscript } from './UnifiedTranscript';

// Historical log event format from the backend
interface RealtimeFeedbackEvent {
    type: string;
    payload: {
        text?: string;
        final?: boolean;
        user_id?: string;
        timestamp?: string;
        function_name?: string;
        tool_call_id?: string;
        result?: string;
        node_name?: string;
        previous_node?: string;
        ttfb_seconds?: number;
        processor?: string;
        model?: string;
    };
    timestamp: string;
    turn: number;
}

export interface WorkflowRunLogs {
    realtime_feedback_events?: RealtimeFeedbackEvent[];
}

// Props for live mode (WebSocket messages)
interface LiveModeProps {
    mode: 'live';
    messages: FeedbackMessage[];
    isCallActive: boolean;
    isCallCompleted: boolean;
}

// Props for historical mode (API logs)
interface HistoricalModeProps {
    mode: 'historical';
    logs: WorkflowRunLogs | null;
}

type RealtimeFeedbackProps = LiveModeProps | HistoricalModeProps;

/**
 * Convert backend log events to unified TranscriptEvent format
 */
function convertLogEventsToTranscriptEvents(events: RealtimeFeedbackEvent[]): TranscriptEvent[] {
    return events.map(event => {
        let type: TranscriptEvent['type'];
        let status: TranscriptEvent['status'];

        switch (event.type) {
            case 'rtf-user-transcription':
                type = 'user-transcription';
                break;
            case 'rtf-bot-text':
                type = 'bot-text';
                break;
            case 'rtf-function-call-start':
                type = 'function-call';
                status = 'running';
                break;
            case 'rtf-function-call-end':
                type = 'function-call';
                status = 'completed';
                break;
            case 'rtf-node-transition':
                type = 'node-transition';
                break;
            case 'rtf-ttfb-metric':
                type = 'ttfb-metric';
                break;
            default:
                type = 'bot-text';
        }

        return {
            type,
            text: event.payload.text || event.payload.result || event.payload.function_name || event.payload.node_name || '',
            final: event.payload.final,
            timestamp: event.timestamp,
            turn: event.turn,
            functionName: event.payload.function_name,
            status,
            nodeName: event.payload.node_name,
            previousNode: event.payload.previous_node,
            ttfbSeconds: event.payload.ttfb_seconds,
            processor: event.payload.processor,
            model: event.payload.model,
        };
    });
}

/**
 * Convert live WebSocket messages to unified TranscriptEvent format
 */
function convertLiveMessagesToTranscriptEvents(messages: FeedbackMessage[]): TranscriptEvent[] {
    return messages.map(msg => ({
        type: msg.type,
        text: msg.text,
        final: msg.final,
        timestamp: msg.timestamp,
        functionName: msg.functionName,
        status: msg.status,
        nodeName: msg.nodeName,
        previousNode: msg.previousNode,
        ttfbSeconds: msg.ttfbSeconds,
        processor: msg.processor,
        model: msg.model,
    }));
}

/**
 * Single unified component that handles both live WebSocket messages
 * and historical logs from the API.
 */
export const RealtimeFeedback = (props: RealtimeFeedbackProps) => {
    if (props.mode === 'historical') {
        // Historical mode - process logs from API
        const rawEvents = props.logs?.realtime_feedback_events;
        const messages = rawEvents
            ? processTranscriptEvents(convertLogEventsToTranscriptEvents(rawEvents))
            : [];

        return (
            <UnifiedTranscript
                messages={messages}
                status="ended"
                title="Call Transcript"
                emptyState={{
                    title: "No conversation recorded",
                    subtitle: "Real-time feedback events were not captured for this call"
                }}
            />
        );
    }

    // Live mode - process WebSocket messages (optimized - messages already accumulated)
    const { messages, isCallActive, isCallCompleted } = props;
    const status = isCallActive ? 'live' : isCallCompleted ? 'ended' : 'ready';
    const processedMessages = processLiveMessages(convertLiveMessagesToTranscriptEvents(messages));

    return (
        <UnifiedTranscript
            messages={processedMessages}
            status={status}
            title="Live Transcript"
            autoScroll={true}
            emptyState={{
                title: "No messages yet",
                subtitle: isCallActive
                    ? "Start speaking to see the transcript"
                    : "Start the call to begin the conversation"
            }}
        />
    );
};
