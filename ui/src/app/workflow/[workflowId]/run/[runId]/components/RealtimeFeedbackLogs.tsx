'use client';

import { CheckCircle, MessageSquare, MicOff,Wrench } from 'lucide-react';

import { cn } from '@/lib/utils';

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
    };
    timestamp: string;
    turn: number;
}

export interface WorkflowRunLogs {
    realtime_feedback_events?: RealtimeFeedbackEvent[];
}

interface RealtimeFeedbackLogsProps {
    logs: WorkflowRunLogs | null;
}

const EventItem = ({ event }: { event: RealtimeFeedbackEvent }) => {
    // Function call message - centered
    if (event.type === 'rtf-function-call-start' || event.type === 'rtf-function-call-end') {
        return (
            <div className="flex justify-center">
                <div className="px-3 py-1.5 rounded-full text-xs bg-amber-500/10 border border-amber-500/20 inline-flex items-center gap-2">
                    {event.type === 'rtf-function-call-start' ? (
                        <Wrench className="h-3 w-3 text-amber-500" />
                    ) : (
                        <CheckCircle className="h-3 w-3 text-green-500" />
                    )}
                    <span className="font-mono text-amber-700 dark:text-amber-400">
                        {event.payload.function_name}()
                    </span>
                    {event.type === 'rtf-function-call-end' && (
                        <span className="text-muted-foreground">✓</span>
                    )}
                </div>
            </div>
        );
    }

    const isUser = event.type === 'rtf-user-transcription';

    // User messages on right, bot messages on left
    return (
        <div className={cn(
            "flex",
            isUser ? "justify-end" : "justify-start"
        )}>
            <div
                className={cn(
                    "max-w-[85%] px-3 py-2 rounded-2xl text-sm",
                    isUser
                        ? "bg-primary text-primary-foreground rounded-br-md"
                        : "bg-muted rounded-bl-md"
                )}
            >
                <div className="whitespace-pre-wrap leading-relaxed">{event.payload.text}</div>
            </div>
        </div>
    );
};

function processEvents(events: RealtimeFeedbackEvent[]): RealtimeFeedbackEvent[] {
    // Filter out interim transcriptions
    const filteredEvents = events.filter(event => {
        if (event.type === 'rtf-user-transcription' && !event.payload.final) {
            return false;
        }
        return true;
    });

    // Combine consecutive rtf-bot-text events by turn
    const processed: RealtimeFeedbackEvent[] = [];
    let currentBotText: RealtimeFeedbackEvent | null = null;

    for (const event of filteredEvents) {
        if (event.type === 'rtf-bot-text') {
            if (currentBotText && currentBotText.turn === event.turn) {
                // Same turn, combine the text
                currentBotText.payload.text = (currentBotText.payload.text || '') + ' ' + (event.payload.text || '');
            } else {
                // Different turn or first bot text
                if (currentBotText) {
                    processed.push(currentBotText);
                }
                // Deep copy to avoid mutating original event
                currentBotText = {
                    ...event,
                    payload: { ...event.payload }
                };
            }
        } else {
            // Not a bot text event
            if (currentBotText) {
                processed.push(currentBotText);
                currentBotText = null;
            }
            processed.push(event);
        }
    }

    // Don't forget the last bot text if there is one
    if (currentBotText) {
        processed.push(currentBotText);
    }

    return processed;
}

export function RealtimeFeedbackLogs({ logs }: RealtimeFeedbackLogsProps) {
    const rawEvents = logs?.realtime_feedback_events;
    const events = rawEvents ? processEvents(rawEvents) : undefined;

    return (
        <div className="w-full h-full flex flex-col bg-background border-l border-border">
            {/* Header */}
            <div className="px-4 py-3 border-b border-border shrink-0">
                <div className="flex items-center justify-center gap-2">
                    <MessageSquare className="h-4 w-4 text-muted-foreground shrink-0" />
                    <span className="font-medium text-sm whitespace-nowrap">Call Transcript</span>
                    <div className="flex items-center gap-1 text-xs px-2 py-0.5 rounded-full shrink-0 bg-muted text-muted-foreground">
                        <MicOff className="h-3 w-3" />
                        <span>Ended</span>
                    </div>
                </div>
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto">
                {!events || events.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-full text-muted-foreground text-sm">
                        <MessageSquare className="h-10 w-10 mb-4 opacity-30" />
                        <p className="font-medium">No conversation recorded</p>
                        <p className="text-xs mt-1 text-center px-4">
                            Real-time feedback events were not captured for this call
                        </p>
                    </div>
                ) : (
                    <div className="space-y-3 p-4">
                        {events.map((event, index) => (
                            <EventItem key={index} event={event} />
                        ))}
                    </div>
                )}
            </div>

            {/* Footer with message count */}
            {events && events.length > 0 && (
                <div className="px-4 py-2 border-t border-border text-xs text-muted-foreground shrink-0">
                    {events.filter(e => e.type === 'rtf-user-transcription' || e.type === 'rtf-bot-text').length} messages
                </div>
            )}
        </div>
    );
}
