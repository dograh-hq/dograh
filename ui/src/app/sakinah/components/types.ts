export interface SakinahSession {
    session_id: string;
    workflow_id: number;
    workflow_run_id: number;
    started_at: string;
    scenario: string;
}

export interface TranscriptTurn {
    role: "user" | "sakinah";
    text: string;
    final: boolean;
    timestamp: string;
}

