import type { StreamTextChatMessageResponse } from "@/client/types.gen";

import { type TextChatSession, toTextChatSession } from "./types";

export function applyTextChatStreamEvent(
    session: TextChatSession | null,
    update: StreamTextChatMessageResponse,
): TextChatSession | null {
    if (update.type === "session" || update.type === "complete") {
        return toTextChatSession(update.session);
    }
    if (update.type !== "turn_event" || !session) return session;
    return {
        ...session,
        session_data: {
            ...session.session_data,
            turns: session.session_data.turns.map(turn => turn.id === update.turn_id
                ? { ...turn, events: [...turn.events, update.event] }
                : turn),
        },
    };
}

// A recovery GET can still contain the empty, pending turn saved before execution.
// Keep the events already received until the server has a final snapshot.
export function reconcileTextChatSession(
    current: TextChatSession | null,
    incoming: TextChatSession,
): TextChatSession {
    if (current?.workflow_run_id !== incoming.workflow_run_id ||
        incoming.session_data.status !== "pending_assistant_turn") return incoming;
    return {
        ...incoming,
        session_data: {
            ...incoming.session_data,
            turns: incoming.session_data.turns.map(turn => {
                const visible = current.session_data.turns.find(item => item.id === turn.id);
                return turn.status === "pending" && visible && visible.events.length > turn.events.length
                    ? { ...turn, events: visible.events }
                    : turn;
            }),
        },
    };
}
