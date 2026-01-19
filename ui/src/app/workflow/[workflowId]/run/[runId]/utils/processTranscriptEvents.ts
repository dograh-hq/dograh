/**
 * Utility to process realtime feedback events into a unified transcript format.
 * Used by both live WebSocket messages and post-call logs.
 */

export interface TranscriptEvent {
    type: 'user-transcription' | 'bot-text' | 'function-call' | 'node-transition' | 'ttfb-metric';
    text: string;
    final?: boolean;
    timestamp: string;
    turn?: number;
    functionName?: string;
    status?: 'running' | 'completed';
    nodeName?: string;
    previousNode?: string;
    ttfbSeconds?: number;
    processor?: string;
    model?: string;
}

export interface ProcessedMessage {
    id: string;
    type: TranscriptEvent['type'];
    text: string;
    final?: boolean;
    timestamp: string;
    functionName?: string;
    status?: 'running' | 'completed';
    nodeName?: string;
    ttfbSeconds?: number;
}

/**
 * Process transcript events (both live and historical).
 * Combines consecutive bot-text by turn and associates TTFB metrics.
 */
export function processTranscriptEvents(events: TranscriptEvent[]): ProcessedMessage[] {
    // Filter out interim transcriptions and function-call-start events
    const filteredEvents = events.filter(event => {
        if (event.type === 'user-transcription' && !event.final) return false;
        if (event.type === 'function-call' && event.status === 'running') return false;
        return true;
    });

    const processed: ProcessedMessage[] = [];
    let currentBotText: { event: TranscriptEvent; text: string } | null = null;
    let pendingTtfb: TranscriptEvent | null = null;

    const flushBotText = () => {
        if (!currentBotText) return;

        processed.push(convertToProcessedMessage(currentBotText.event, currentBotText.text));

        // Add the pending TTFB metric if it exists
        if (pendingTtfb) {
            processed.push(convertToProcessedMessage(pendingTtfb));
            pendingTtfb = null;
        }

        currentBotText = null;
    };

    for (const event of filteredEvents) {
        if (event.type === 'ttfb-metric') {
            // Store TTFB to associate with the next bot-text or function-call
            pendingTtfb = event;
        } else if (event.type === 'bot-text') {
            // Combine consecutive bot-text from the same turn
            if (currentBotText && currentBotText.event.turn === event.turn) {
                currentBotText.text = currentBotText.text + ' ' + event.text;
            } else {
                flushBotText();
                currentBotText = { event, text: event.text };
            }
        } else {
            // Handle other events (user-transcription, function-call, node-transition)
            flushBotText();
            processed.push(convertToProcessedMessage(event));

            // Add pending TTFB after function calls
            if (event.type === 'function-call' && pendingTtfb) {
                processed.push(convertToProcessedMessage(pendingTtfb));
                pendingTtfb = null;
            }
        }
    }

    // Flush any remaining bot text
    flushBotText();

    return processed;
}

/**
 * Process live messages - optimized version.
 *
 * Optimizations rely on useWebSocketRTC.tsx already handling:
 * - Bot text accumulation (consecutive chunks combined with spaces)
 * - Interim transcription filtering (only final transcriptions kept)
 * - Function call status (start events filtered, only completed kept)
 *
 * This function only needs to:
 * - Associate TTFB metrics with the preceding bot-text or function-call
 * - Convert to ProcessedMessage format
 */
export function processLiveMessages(messages: TranscriptEvent[]): ProcessedMessage[] {
    const processed: ProcessedMessage[] = [];
    let pendingTtfb: TranscriptEvent | null = null;

    for (const msg of messages) {
        if (msg.type === 'ttfb-metric') {
            // Store TTFB to associate with next message
            pendingTtfb = msg;
        } else {
            // Add the message
            processed.push(convertToProcessedMessage(msg));

            // Add pending TTFB after final bot-text or completed function calls
            if ((msg.type === 'bot-text' && msg.final) ||
                (msg.type === 'function-call' && msg.status === 'completed')) {
                if (pendingTtfb) {
                    processed.push(convertToProcessedMessage(pendingTtfb));
                    pendingTtfb = null;
                }
            }
        }
    }

    return processed;
}

// Alias for backward compatibility
export const processHistoricalEvents = processTranscriptEvents;

function convertToProcessedMessage(event: TranscriptEvent, overrideText?: string): ProcessedMessage {
    return {
        id: `${event.type}-${event.timestamp}`,
        type: event.type,
        text: overrideText ?? event.text,
        final: event.final ?? true,
        timestamp: event.timestamp,
        functionName: event.functionName,
        status: event.status,
        nodeName: event.nodeName,
        ttfbSeconds: event.ttfbSeconds,
    };
}
