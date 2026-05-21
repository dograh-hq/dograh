import type { WorkflowRunTextSessionResponse } from "@/client/types.gen";
import type { ConversationNodeTransitionItem } from "@/components/workflow/conversation";

export interface TextChatMessage {
    text: string;
    created_at: string;
}

export interface TextChatTurn {
    id: string;
    status: string;
    created_at: string;
    user_message: TextChatMessage | null;
    assistant_message: TextChatMessage | null;
    events: Array<Record<string, unknown>>;
    usage: Record<string, unknown>;
}

export interface TextChatSessionData {
    version: number;
    status: string;
    cursor_turn_id: string | null;
    turns: TextChatTurn[];
    discarded_future: Array<Record<string, unknown>>;
    simulator: {
        enabled: boolean;
        config: Record<string, unknown>;
    };
}

export interface TextChatCheckpoint {
    version: number;
    anchor_turn_id: string | null;
    current_node_id: string | null;
    messages: Array<Record<string, unknown>>;
    gathered_context: Record<string, unknown>;
    tool_state: Record<string, unknown>;
}

export type TextChatSession = Omit<WorkflowRunTextSessionResponse, "session_data" | "checkpoint"> & {
    session_data: TextChatSessionData;
    checkpoint: TextChatCheckpoint;
};

export interface TurnActionState {
    turnId: string;
    type: "rewind" | "edit";
}

export type WorkflowRuntimeNodeTransition = ConversationNodeTransitionItem;

export const EMPTY_TEXT_CHAT_TURNS: TextChatTurn[] = [];

export function toTextChatSession(response: WorkflowRunTextSessionResponse): TextChatSession {
    return {
        ...response,
        session_data: response.session_data as unknown as TextChatSessionData,
        checkpoint: response.checkpoint as unknown as TextChatCheckpoint,
    };
}
