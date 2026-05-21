export type ConversationStatus = "ready" | "live" | "ended";

export type RealtimeFeedbackMessageType =
    | "user-transcription"
    | "bot-text"
    | "function-call"
    | "node-transition"
    | "ttfb-metric"
    | "pipeline-error"
    | "interrupt-warning";

export interface RealtimeFeedbackMessage {
    id: string;
    type: RealtimeFeedbackMessageType;
    text: string;
    final?: boolean;
    timestamp: string;
    functionName?: string;
    toolCallId?: string;
    arguments?: unknown;
    result?: unknown;
    status?: "running" | "completed";
    nodeId?: string;
    nodeName?: string;
    previousNodeId?: string;
    previousNode?: string;
    allowInterrupt?: boolean;
    ttfbSeconds?: number;
    processor?: string;
    model?: string;
    fatal?: boolean;
}

export interface RealtimeFeedbackEvent {
    type: string;
    payload: {
        text?: string;
        final?: boolean;
        user_id?: string;
        timestamp?: string;
        function_name?: string;
        tool_call_id?: string;
        arguments?: unknown;
        result?: unknown;
        node_id?: string;
        node_name?: string;
        previous_node_id?: string;
        previous_node?: string;
        previous_node_name?: string;
        allow_interrupt?: boolean;
        ttfb_seconds?: number;
        processor?: string;
        model?: string;
        error?: string;
        fatal?: boolean;
    };
    timestamp: string;
    turn: number;
}

export interface WorkflowRunLogs {
    realtime_feedback_events?: RealtimeFeedbackEvent[];
}

interface ConversationItemBase {
    id: string;
    timestamp?: string;
    turnId?: string;
    reasoningDurationMs?: number;
}

export interface ConversationMessageItem extends ConversationItemBase {
    kind: "message";
    role: "user" | "assistant";
    text: string;
    final?: boolean;
    tone?: "default" | "muted";
}

export interface ConversationToolCallItem extends ConversationItemBase {
    kind: "tool-call";
    functionName: string;
    toolCallId?: string;
    status: "running" | "completed";
    arguments?: unknown;
    result?: unknown;
}

export interface ConversationNodeTransitionItem extends ConversationItemBase {
    kind: "node-transition";
    nodeId?: string;
    nodeName: string;
    previousNodeId?: string;
    previousNodeName?: string;
    allowInterrupt?: boolean;
}

export interface ConversationNoticeItem extends ConversationItemBase {
    kind: "notice";
    tone: "warning" | "error";
    title: string;
    text: string;
    fatal?: boolean;
    linkHref?: string;
    linkLabel?: string;
}

export type ConversationItem =
    | ConversationMessageItem
    | ConversationToolCallItem
    | ConversationNodeTransitionItem
    | ConversationNoticeItem;

export interface ConversationEmptyStateData {
    title: string;
    subtitle: string;
}
