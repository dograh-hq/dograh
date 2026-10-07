import type { ConversationItem } from "../types";

interface TextChatMessageLike {
    text?: string;
    created_at?: string;
}

interface TextChatEventLike {
    type?: unknown;
    payload?: unknown;
    created_at?: unknown;
}

interface TextChatTurnLike {
    id: string;
    status?: string;
    created_at?: string;
    user_message?: TextChatMessageLike | null;
    assistant_message?: TextChatMessageLike | null;
    events?: Array<Record<string, unknown>>;
}

function asRecord(value: unknown) {
    return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

function asString(value: unknown) {
    return typeof value === "string" ? value : undefined;
}

function conversationItemsFromTextChatEvents(
    events: Array<Record<string, unknown>>,
    turnId: string,
    fallbackTimestamp?: string,
) {
    const items: ConversationItem[] = [];
    const toolCallIndexById = new Map<string, number>();

    events.forEach((rawEvent, index) => {
        const event = rawEvent as TextChatEventLike;
        const eventType = asString(event.type);
        const payload = asRecord(event.payload);
        if (!eventType || !payload) {
            return;
        }

        const timestamp = asString(event.created_at) ?? fallbackTimestamp;

        if (eventType === "node_transition") {
            const nodeName = asString(payload.node_name) ?? "Node";
            items.push({
                kind: "node-transition",
                id: `${turnId}-node-${index}`,
                turnId,
                timestamp,
                nodeId: asString(payload.node_id),
                nodeName,
                previousNodeId: asString(payload.previous_node_id),
                previousNodeName: asString(payload.previous_node_name),
                allowInterrupt: typeof payload.allow_interrupt === "boolean" ? payload.allow_interrupt : undefined,
            });
            return;
        }

        if (eventType === "bot_speech") {
            const text = asString(payload.text);
            if (text) {
                items.push({
                    kind: "message",
                    id: `${turnId}-bot-speech-${index}`,
                    turnId,
                    timestamp,
                    role: "assistant",
                    text,
                });
            }
            return;
        }

        if (eventType === "execution_error") {
            items.push({
                kind: "notice",
                id: `${turnId}-error-${index}`,
                turnId,
                timestamp,
                tone: "error",
                title: "Execution Error",
                text: asString(payload.message) ?? "Execution error",
                fatal: true,
            });
            return;
        }

        if (eventType === "tool_call_started") {
            const functionName = asString(payload.function_name) ?? "tool";
            const toolCallId = asString(payload.tool_call_id);
            items.push({
                kind: "tool-call",
                id: toolCallId ?? `${turnId}-tool-${index}`,
                turnId,
                timestamp,
                functionName,
                toolCallId,
                status: "running",
                arguments: payload.arguments,
            });
            if (toolCallId) {
                toolCallIndexById.set(toolCallId, items.length - 1);
            }
            return;
        }

        if (eventType === "tool_call_result") {
            const functionName = asString(payload.function_name) ?? "tool";
            const toolCallId = asString(payload.tool_call_id);
            const existingIndex = toolCallId ? toolCallIndexById.get(toolCallId) : undefined;

            if (existingIndex !== undefined) {
                const existingItem = items[existingIndex];
                if (existingItem?.kind === "tool-call") {
                    items[existingIndex] = {
                        ...existingItem,
                        status: "completed",
                        result: payload.result,
                    };
                }
                return;
            }

            items.push({
                kind: "tool-call",
                id: toolCallId ?? `${turnId}-tool-result-${index}`,
                turnId,
                timestamp,
                functionName,
                toolCallId,
                status: "completed",
                result: payload.result,
            });
        }
    });

    return items;
}

export function conversationItemsFromTextChatTurns(turns: TextChatTurnLike[]) {
    const items: ConversationItem[] = [];

    turns.forEach((turn) => {
        if (turn.user_message?.text) {
            items.push({
                kind: "message",
                id: `${turn.id}-user`,
                turnId: turn.id,
                timestamp: turn.user_message.created_at ?? turn.created_at,
                role: "user",
                text: turn.user_message.text,
            });
        }

        const eventItems = conversationItemsFromTextChatEvents(
            turn.events ?? [],
            turn.id,
            turn.created_at,
        );

        if (turn.assistant_message?.text) {
            let remainingText = turn.assistant_message.text.trim();
            const segmentsBeforeEventIndex = new Map<number, string>();

            eventItems.forEach((eventItem, index) => {
                if (eventItem.kind === "message" && eventItem.role === "assistant" && eventItem.text) {
                    const idx = remainingText.indexOf(eventItem.text);
                    if (idx !== -1) {
                        const before = remainingText.slice(0, idx).trim();
                        remainingText = remainingText.slice(idx + eventItem.text.length).trim();
                        if (before) {
                            segmentsBeforeEventIndex.set(index, before);
                        }
                    }
                }
            });

            // If an assistant segment occurred before an announcement, and that announcement
            // was preceded by a transition sequence (node_transition and/or the transition tool call),
            // the assistant segment was uttered before the transition sequence was initiated.
            // We bound this check to the immediate node-transition (and its preceding transition tool call)
            // so we do not move assistant text before prior completed data lookup tools.
            const segmentBeforeIndexToEmitAt = new Map<number, string>();
            segmentsBeforeEventIndex.forEach((text, speechIdx) => {
                let emitIdx = speechIdx;
                if (emitIdx > 0 && eventItems[emitIdx - 1]?.kind === "node-transition") {
                    emitIdx--;
                    if (emitIdx > 0 && eventItems[emitIdx - 1]?.kind === "tool-call") {
                        emitIdx--;
                    }
                }
                segmentBeforeIndexToEmitAt.set(emitIdx, text);
            });

            eventItems.forEach((eventItem, index) => {
                const segmentBefore = segmentBeforeIndexToEmitAt.get(index);
                if (segmentBefore) {
                    items.push({
                        kind: "message",
                        id: `${turn.id}-assistant-segment-${index}`,
                        turnId: turn.id,
                        timestamp: turn.assistant_message!.created_at ?? turn.created_at,
                        role: "assistant",
                        text: segmentBefore,
                    });
                }
                items.push(eventItem);
            });

            if (remainingText) {
                items.push({
                    kind: "message",
                    id: `${turn.id}-assistant`,
                    turnId: turn.id,
                    timestamp: turn.assistant_message.created_at ?? turn.created_at,
                    role: "assistant",
                    text: remainingText,
                });
            }
            return;
        }

        items.push(...eventItems);

        if (turn.status === "failed") {
            items.push({
                kind: "message",
                id: `${turn.id}-assistant-failed`,
                turnId: turn.id,
                timestamp: turn.created_at,
                role: "assistant",
                text: "Agent turn failed",
                tone: "muted",
            });
        }
    });

    return items;
}
