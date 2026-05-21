import type {
    ConversationItem,
    RealtimeFeedbackEvent,
    RealtimeFeedbackMessage,
} from "../types";

function feedbackEventText(event: RealtimeFeedbackEvent) {
    return (
        event.payload.text ??
        event.payload.error ??
        (typeof event.payload.result === "string" ? event.payload.result : undefined) ??
        event.payload.function_name ??
        event.payload.node_name ??
        ""
    );
}

function liveFeedbackItem(message: RealtimeFeedbackMessage, reasoningDurationMs?: number): ConversationItem | null {
    if (message.type === "ttfb-metric") {
        return null;
    }

    if (message.type === "user-transcription") {
        return {
            kind: "message",
            id: message.id,
            timestamp: message.timestamp,
            role: "user",
            text: message.text,
            final: message.final,
        };
    }

    if (message.type === "bot-text") {
        return {
            kind: "message",
            id: message.id,
            timestamp: message.timestamp,
            role: "assistant",
            text: message.text,
            final: message.final,
            reasoningDurationMs,
        };
    }

    if (message.type === "function-call") {
        return {
            kind: "tool-call",
            id: message.id,
            timestamp: message.timestamp,
            functionName: message.functionName ?? "tool",
            toolCallId: message.toolCallId,
            arguments: message.arguments,
            result: message.result,
            status: message.status ?? "completed",
            reasoningDurationMs,
        };
    }

    if (message.type === "node-transition") {
        return {
            kind: "node-transition",
            id: message.id,
            timestamp: message.timestamp,
            nodeId: message.nodeId,
            nodeName: message.nodeName ?? message.text,
            previousNodeId: message.previousNodeId,
            previousNodeName: message.previousNode,
            allowInterrupt: message.allowInterrupt,
        };
    }

    if (message.type === "interrupt-warning") {
        return {
            kind: "notice",
            id: message.id,
            timestamp: message.timestamp,
            tone: "warning",
            title: "Interruption Disabled",
            text: message.text,
            linkHref: "https://docs.dograh.com/configurations/interruption",
            linkLabel: "Learn more",
        };
    }

    if (message.type === "pipeline-error") {
        return {
            kind: "notice",
            id: message.id,
            timestamp: message.timestamp,
            tone: "error",
            title: message.fatal ? "Fatal Pipeline Error" : "Pipeline Error",
            text: message.text,
            fatal: message.fatal,
        };
    }

    return null;
}

export function conversationItemsFromLiveFeedback(messages: RealtimeFeedbackMessage[]) {
    const items: ConversationItem[] = [];
    let pendingReasoningDurationMs: number | undefined;

    messages.forEach((message) => {
        if (message.type === "ttfb-metric") {
            if (message.ttfbSeconds !== undefined) {
                pendingReasoningDurationMs = message.ttfbSeconds * 1000;
            }
            return;
        }

        const item = liveFeedbackItem(message, pendingReasoningDurationMs);
        if (!item) {
            return;
        }

        items.push(item);

        if (item.kind === "message" || item.kind === "tool-call") {
            pendingReasoningDurationMs = undefined;
        }
    });

    return items;
}

export function conversationItemsFromRealtimeFeedbackEvents(events: RealtimeFeedbackEvent[]) {
    const items: ConversationItem[] = [];
    const toolCallIndexById = new Map<string, number>();
    let pendingReasoningDurationMs: number | undefined;
    let currentBotItemIndex: number | null = null;
    let currentBotTurn: number | null = null;

    events.forEach((event, index) => {
        if (event.type === "rtf-ttfb-metric") {
            if (event.payload.ttfb_seconds !== undefined) {
                pendingReasoningDurationMs = event.payload.ttfb_seconds * 1000;
            }
            return;
        }

        if (event.type === "rtf-user-transcription") {
            currentBotItemIndex = null;
            currentBotTurn = null;
            items.push({
                kind: "message",
                id: `user-${event.turn}-${index}`,
                timestamp: event.timestamp,
                role: "user",
                text: feedbackEventText(event),
                final: event.payload.final,
            });
            return;
        }

        if (event.type === "rtf-bot-text") {
            const text = feedbackEventText(event);
            const lastItem = currentBotItemIndex !== null ? items[currentBotItemIndex] : null;

            if (
                currentBotItemIndex !== null &&
                currentBotTurn === event.turn &&
                lastItem?.kind === "message" &&
                lastItem.role === "assistant"
            ) {
                items[currentBotItemIndex] = {
                    ...lastItem,
                    text: `${lastItem.text} ${text}`.trim(),
                };
                return;
            }

            items.push({
                kind: "message",
                id: `bot-${event.turn}-${index}`,
                timestamp: event.timestamp,
                role: "assistant",
                text,
                final: event.payload.final,
                reasoningDurationMs: pendingReasoningDurationMs,
            });
            currentBotItemIndex = items.length - 1;
            currentBotTurn = event.turn;
            pendingReasoningDurationMs = undefined;
            return;
        }

        currentBotItemIndex = null;
        currentBotTurn = null;

        if (event.type === "rtf-function-call-start") {
            const toolCallId = event.payload.tool_call_id;
            items.push({
                kind: "tool-call",
                id: toolCallId ?? `tool-${event.turn}-${index}`,
                timestamp: event.timestamp,
                functionName: event.payload.function_name ?? "tool",
                toolCallId,
                arguments: event.payload.arguments,
                status: "running",
                reasoningDurationMs: pendingReasoningDurationMs,
            });
            if (toolCallId) {
                toolCallIndexById.set(toolCallId, items.length - 1);
            }
            pendingReasoningDurationMs = undefined;
            return;
        }

        if (event.type === "rtf-function-call-end") {
            const toolCallId = event.payload.tool_call_id;
            const existingIndex = toolCallId ? toolCallIndexById.get(toolCallId) : undefined;

            if (existingIndex !== undefined) {
                const existingItem = items[existingIndex];
                if (existingItem?.kind === "tool-call") {
                    items[existingIndex] = {
                        ...existingItem,
                        status: "completed",
                        result: event.payload.result,
                    };
                }
                return;
            }

            items.push({
                kind: "tool-call",
                id: toolCallId ?? `tool-result-${event.turn}-${index}`,
                timestamp: event.timestamp,
                functionName: event.payload.function_name ?? "tool",
                toolCallId,
                result: event.payload.result,
                status: "completed",
                reasoningDurationMs: pendingReasoningDurationMs,
            });
            pendingReasoningDurationMs = undefined;
            return;
        }

        if (event.type === "rtf-node-transition") {
            items.push({
                kind: "node-transition",
                id: `node-${event.turn}-${index}`,
                timestamp: event.timestamp,
                nodeId: event.payload.node_id,
                nodeName: event.payload.node_name ?? feedbackEventText(event) ?? "Node",
                previousNodeId: event.payload.previous_node_id,
                previousNodeName: event.payload.previous_node_name ?? event.payload.previous_node,
                allowInterrupt: event.payload.allow_interrupt,
            });
            return;
        }

        if (event.type === "rtf-interrupt-warning") {
            items.push({
                kind: "notice",
                id: `warning-${event.turn}-${index}`,
                timestamp: event.timestamp,
                tone: "warning",
                title: "Interruption Disabled",
                text: feedbackEventText(event),
                linkHref: "https://docs.dograh.com/configurations/interruption",
                linkLabel: "Learn more",
            });
            return;
        }

        if (event.type === "rtf-pipeline-error") {
            items.push({
                kind: "notice",
                id: `error-${event.turn}-${index}`,
                timestamp: event.timestamp,
                tone: "error",
                title: event.payload.fatal ? "Fatal Pipeline Error" : "Pipeline Error",
                text: feedbackEventText(event),
                fatal: event.payload.fatal,
            });
        }
    });

    return items;
}
